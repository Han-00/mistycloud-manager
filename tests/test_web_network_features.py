"""分流应用、异步诊断、异常卡片及运行守卫的离线回归。"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config
from switcher import Switcher
from ui_web import WebAppUI, _JsApi


class Pool:
    def get_active(self):
        return None

    def all(self):
        return []


class Engine:
    port = 10808
    http_port = 10809

    def __init__(self, config, running=True):
        self.config = config
        self.running = running
        self.current_node = {"id": "fake", "address": "node.example.com", "port": 443}
        self.writes = []
        self.starts = []
        self.start_results = []

    def is_running(self):
        return self.running

    def write_config(self, node):
        self.writes.append(self.config.get("proxy_domain_rules"))

    def start(self):
        self.starts.append(1)
        return self.start_results.pop(0) if self.start_results else True

    def wait_port(self, timeout=15):
        return True


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class NetworkFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(path=os.path.join(self.tmp.name, "settings.json"))
        self.pool = Pool()
        self.eng = Engine(self.cfg)
        self.sw = Switcher(self.cfg, self.pool, self.eng)
        self.ui = WebAppUI(self.cfg, self.pool, self.sw, self.eng)
        self.api = _JsApi(self.ui)
        self.rules = [{"domain": "claude.ai", "action": "proxy"}]
        self.old_rules = [{"domain": "example.com", "action": "direct"}]
        self.cfg.set("proxy_domain_rules", self.old_rules)
        self.sys = patch("sysproxy.available", return_value=False)
        self.sys.start()

    def tearDown(self):
        self.sys.stop()
        self.tmp.cleanup()

    def test_saved_and_running_rules_are_the_same(self):
        result = self.ui._apply_routing_rules(self.rules)
        self.assertTrue(result["ok"])
        self.assertTrue(result["applied"])
        with open(self.cfg.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["proxy_domain_rules"], self.rules)
        self.assertEqual(self.eng.writes, [self.rules])

    def test_failed_restart_rolls_back_memory_and_engine(self):
        self.cfg.save()
        self.eng.start_results = [False, True]
        result = self.ui._apply_routing_rules(self.rules)
        self.assertFalse(result["ok"])
        self.assertTrue(result["recovered"])
        self.assertEqual(self.cfg.get("proxy_domain_rules"), self.old_rules)
        self.assertEqual(self.eng.writes, [self.rules, self.old_rules])
        with open(self.cfg.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["proxy_domain_rules"], self.old_rules)

    def test_failed_persistence_rolls_back_engine(self):
        with patch.object(self.cfg, "save", return_value=False):
            result = self.ui._apply_routing_rules(self.rules)
        self.assertFalse(result["ok"])
        self.assertTrue(result["recovered"])
        self.assertEqual(self.eng.writes, [self.rules, self.old_rules])
        self.assertEqual(self.cfg.get("proxy_domain_rules"), self.old_rules)

    def test_sysproxy_failure_is_not_success(self):
        self.cfg.set("system_proxy", True)
        with patch("sysproxy.available", return_value=True), patch(
                "sysproxy.apply_if_enabled", side_effect=[False, True]):
            result = self.ui._apply_routing_rules(self.rules)
        self.assertFalse(result["ok"])
        self.assertTrue(result["recovered"])
        self.assertEqual(self.cfg.get("proxy_domain_rules"), self.old_rules)

    def test_stopped_engine_saves_without_starting(self):
        self.eng.running = False
        result = self.ui._apply_routing_rules(self.rules)
        self.assertTrue(result["ok"])
        self.assertFalse(result["applied"])
        self.assertEqual(self.eng.starts, [])

    def test_closing_during_config_write_does_not_restart(self):
        write = self.eng.write_config
        def closing_write(node):
            write(node)
            self.ui._closed = True
        self.eng.write_config = closing_write
        result = self.ui._apply_routing_rules(self.rules)
        self.assertFalse(result["ok"])
        self.assertEqual(self.eng.starts, [])
        self.assertEqual(self.cfg.get("proxy_domain_rules"), self.old_rules)

    def test_closed_ui_rejects_restart_switch_and_system_proxy(self):
        self.ui._closed = True
        with patch.object(self.sw, "auto_switch") as auto, patch.object(
                self.sw, "switch_to_email") as manual, patch("sysproxy.disable") as disable:
            self.ui._restart_engine_async("端口")
            self.ui.manual_switch()
            self.ui.switch_to_email("fake@example.com")
            self.ui.set_sysproxy(True)
        auto.assert_not_called()
        manual.assert_not_called()
        disable.assert_not_called()
        self.assertFalse(self.cfg.get("system_proxy"))
        self.assertEqual(self.eng.starts, [])

    def test_busy_system_proxy_toggle_preserves_configuration(self):
        with self.sw.runtime_update_guard(), patch.object(self.ui, "push_state"), patch(
                "sysproxy.apply_if_enabled") as apply, patch("sysproxy.disable") as disable:
            self.ui.set_sysproxy(True)
        self.assertFalse(self.cfg.get("system_proxy"))
        apply.assert_not_called()
        disable.assert_not_called()

    def test_shutdown_restores_backup_even_when_expected_proxy_is_off(self):
        with patch("sysproxy.available", return_value=True), patch(
                "sysproxy.has_backup", return_value=True), patch("sysproxy.disable") as disable:
            self.ui._restore_sysproxy()
        disable.assert_called_once()

    def test_monitor_proxy_reconciliation_skips_busy_and_stopped_runtime(self):
        from monitor import Monitor
        monitor = Monitor(self.cfg, self.pool, self.sw, self.eng)
        with patch("sysproxy.apply_if_enabled") as apply:
            with self.sw.runtime_update_guard():
                monitor._sysproxy_reconcile()
            monitor.stop()
            monitor._sysproxy_reconcile()
        apply.assert_not_called()

    def test_shutdown_waits_for_active_operation_before_restore_and_stop(self):
        active = threading.Event()
        release = threading.Event()
        stopping = threading.Event()
        done = threading.Event()
        actions = []
        self.ui.monitor = SimpleNamespace(stop=stopping.set)
        self.eng.stop = lambda: actions.append("stop")
        def operation():
            with self.sw.runtime_update_guard() as acquired:
                if acquired:
                    active.set()
                    release.wait(2)
                    actions.append("start")
        worker = threading.Thread(target=operation)
        worker.start()
        self.assertTrue(active.wait(1))
        def shutdown():
            self.ui._shutdown()
            done.set()
        with patch.object(self.ui, "_restore_sysproxy", side_effect=lambda: actions.append("restore")):
            closer = threading.Thread(target=shutdown)
            closer.start()
            try:
                self.assertTrue(stopping.wait(1))
                self.assertFalse(done.wait(0.02))
            finally:
                release.set()
                worker.join(timeout=2)
                closer.join(timeout=2)
        self.assertTrue(done.is_set())
        self.assertEqual(actions, ["start", "restore", "stop"])

    def test_shutdown_cancels_settings_restart_while_config_write_is_pending(self):
        writing = threading.Event()
        release = threading.Event()
        stopping = threading.Event()
        done = threading.Event()
        self.ui.monitor = SimpleNamespace(stop=stopping.set)
        self.eng.stop = lambda: None
        def blocked_write(node):
            writing.set()
            release.wait(2)
        self.eng.write_config = blocked_write
        self.ui._restart_engine_async("端口")
        self.assertTrue(writing.wait(1))
        def shutdown():
            self.ui._shutdown()
            done.set()
        with patch.object(self.ui, "_restore_sysproxy"):
            closer = threading.Thread(target=shutdown)
            closer.start()
            try:
                self.assertTrue(stopping.wait(1))
                self.assertFalse(done.wait(0.02))
            finally:
                release.set()
                closer.join(timeout=2)
        self.assertTrue(done.is_set())
        self.assertEqual(self.eng.starts, [])

    def test_bad_hourly_records_keep_valid_data_for_anomaly_analysis(self):
        import stats
        import anomaly
        valid = time.strftime("%Y-%m-%dT%H", time.localtime(time.time() - 3600))
        invalid = time.strftime("%Y-%m-%dT%H", time.localtime())
        database = stats.Stats(os.path.join(self.tmp.name, "stats.json"))
        database.data["hourly"] = {
            valid: 300 * 1024 * 1024, invalid: "garbage", "bad-hour": 1,
            None: 100, "2099-01-01T01": float("inf"), "2099-01-01T02": None,
        }
        self.assertEqual(database.hourly_series(), [[valid, 300 * 1024 * 1024]])
        with patch("stats.hourly_series", side_effect=database.hourly_series), patch(
                "events.recent", return_value=[]):
            rows = anomaly.detect()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hour"], valid)

    def test_other_saver_cannot_persist_unverified_rules(self):
        write = self.eng.write_config
        started = threading.Event()
        finished = threading.Event()
        def save_other_settings():
            started.set()
            self.cfg.set("win_x", 20)
            self.cfg.save()
            finished.set()
        worker = threading.Thread(target=save_other_settings)
        def staged_write(node):
            write(node)
            if len(self.eng.writes) == 1:
                worker.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.02))
        self.eng.write_config = staged_write
        self.eng.start_results = [False, True]
        result = self.ui._apply_routing_rules(self.rules)
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(result["ok"])
        with open(self.cfg.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["proxy_domain_rules"], self.old_rules)

    def test_bad_rule_and_busy_guard_do_not_touch_engine(self):
        self.assertFalse(self.api.save_routing_rules([{"domain": "bad/path", "action": "direct"}])["ok"])
        with self.sw.runtime_update_guard() as acquired:
            self.assertTrue(acquired)
            self.assertFalse(self.api.save_routing_rules(self.rules)["ok"])
            self.assertFalse(self.sw.auto_switch().ok)
            with self.sw.runtime_update_guard() as nested:
                self.assertFalse(nested)
            self.assertTrue(self.sw.is_switching())
        self.assertFalse(self.sw.is_switching())
        self.assertEqual(self.eng.writes, [])

    def test_exception_releases_guard(self):
        with self.assertRaises(RuntimeError):
            with self.sw.runtime_update_guard():
                raise RuntimeError("fake")
        self.assertFalse(self.sw.is_switching())

    def test_async_save_reports_completion_and_clears_busy(self):
        with patch("ui_web.threading.Thread", ImmediateThread):
            self.assertEqual(self.api.save_routing_rules(self.rules), {"ok": True, "pending": True})
        self.assertFalse(self.ui._rules_pending)
        self.assertFalse(self.sw.is_switching())
        queued = []
        while not self.ui._q.empty():
            queued.append(self.ui._q.get())
        self.assertTrue(any("App.rulesSaved(" in s and '"ok": true' in s for s in queued))

    def test_preview_does_not_modify_configuration(self):
        self.cfg.set("proxy_domain_rules", [{"domain": "qq.com", "action": "proxy"}])
        result = self.api.preview_domain("https://www.qq.com/path?token=secret")
        self.assertEqual(result["domain"], "www.qq.com")
        self.assertEqual(result["action"], "proxy")
        self.assertEqual(result["source"], "user")
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(self.eng.writes, [])

    def test_anomaly_state_cache_and_error_isolation(self):
        item = {"hour": "2026-10-03T10", "bytes": 250 * 1024 * 1024,
                "baseline": None, "accounts": [], "inferred": True, "basis": "threshold"}
        with patch("anomaly.detect", return_value=[item]) as detect:
            first = self.ui._traffic_anomalies(True)
            self.assertEqual(first["items"], [item])
            self.assertEqual(self.ui._traffic_anomalies(True), first)
            detect.assert_called_once_with(days=7)
        self.ui._anomalies_cache = None
        with patch("anomaly.detect", side_effect=ValueError("bad")):
            result = self.ui._traffic_anomalies(True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["items"], [])

    def test_build_state_transmits_anomaly_without_live_data(self):
        with patch("stats.hourly_series", return_value=[["2026-10-03T10", 250]]), patch(
                "stats.avg_daily_bytes", return_value=0), patch(
                "stats.availability", return_value={"pct": None}), patch(
                "anomaly.detect", return_value=[]):
            state = self.ui.build_state()
        self.assertEqual(state["traffic_anomalies"]["status"], "ready")

    def test_diagnosis_happens_in_worker_and_failures_release_lock(self):
        with patch("proxy_diagnostics.build_diagnostics", return_value={"applications": {}}) as build, patch(
                "ui_web.threading.Thread", ImmediateThread):
            result = self.api.diagnose_proxy("Claude", "https://claude.ai/path?token=secret")
        self.assertTrue(result["ok"])
        self.assertEqual(build.call_args.args[3], "claude.ai")
        self.assertTrue(any("App.renderDiagnostics(" in self.ui._q.get()
                            for _ in range(self.ui._q.qsize())))
        with patch("proxy_diagnostics.build_diagnostics", side_effect=RuntimeError()), patch(
                "ui_web.threading.Thread", ImmediateThread):
            self.api.diagnose_proxy()
        self.assertTrue(self.ui._diagnosis_lock.acquire(blocking=False))
        self.ui._diagnosis_lock.release()

    def test_concurrent_diagnosis_is_rejected(self):
        with self.ui._diagnosis_lock:
            self.assertFalse(self.api.diagnose_proxy()["ok"])


if __name__ == "__main__":
    unittest.main()
