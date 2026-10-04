"""退出/运行时互斥离线测试：Event 控制并发，全部依赖为假对象。"""
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import _bootstrap
from switcher import Switcher, SwitchResult


class RuntimeShutdownTests(unittest.TestCase):
    def setUp(self):
        self.pool = Mock()
        self.pool.get_active.return_value = {"email": "active@example.com", "password": "fake"}
        self.pool.all.return_value = [{"email": "candidate@example.com", "password": "fake"}]
        self.pool.count.return_value = 1
        self.engine = Mock()
        self.engine._discover.return_value = True
        self.engine.is_running.return_value = False
        self.engine.start.return_value = True
        self.monitor = Mock()
        self.config = {"reserve_accounts": 2}
        self.sw = Switcher(self.config, self.pool, self.engine, log=Mock())
        self.errors = []
        for name in ("switcher.events.record", "switcher.notify_switch", "stats.record_switch",
                     "sysproxy.apply_if_enabled"):
            patcher = patch(name)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            setattr(self, name.split(".")[-1], mocked)

    def spawn(self, target, release=None):
        def run():
            try:
                target()
            except BaseException as exc:
                self.errors.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        if release:
            self.addCleanup(release.set)
        return thread

    def wait_closing(self):
        with self.sw._condition:
            self.assertTrue(self.sw._condition.wait_for(lambda: self.sw._closing, timeout=3))

    def join(self, *threads):
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive(), "离线操作未释放")
        self.assertEqual(self.errors, [])

    def begin_shutdown(self):
        finished = threading.Event()
        thread = self.spawn(lambda: (self.sw.begin_shutdown(), finished.set()))
        self.wait_closing()
        return thread, finished

    def block(self, entered, release):
        entered.set()
        self.assertTrue(release.wait(3), "离线测试未释放操作")

    def test_shutdown_waits_for_runtime_and_rejects_new_operations(self):
        entered, release = threading.Event(), threading.Event()
        def update():
            with self.sw.runtime_update_guard() as acquired:
                self.assertTrue(acquired)
                self.block(entered, release)
        operation = self.spawn(update, release)
        self.assertTrue(entered.wait(3))
        shutdown, finished = self.begin_shutdown()
        self.assertFalse(finished.is_set())
        with self.sw.runtime_update_guard() as acquired:
            self.assertFalse(acquired)
        self.assertIn("退出", self.sw.switch_to_email("candidate@example.com").error)
        self.assertIn("退出", self.sw.auto_switch().error)
        self.engine.write_config.assert_not_called()
        release.set()
        self.join(operation, shutdown)
        self.assertTrue(finished.is_set())
        self.assertFalse(self.sw.is_switching())
        with self.sw.runtime_update_guard() as acquired:
            self.assertFalse(acquired)

    def test_waiting_runtime_guard_is_rejected_on_closing(self):
        entered, release, waiting_done = threading.Event(), threading.Event(), threading.Event()
        def owner():
            with self.sw.runtime_update_guard():
                self.block(entered, release)
        operation = self.spawn(owner, release)
        self.assertTrue(entered.wait(3))
        def waiter():
            with self.sw.runtime_update_guard(wait=True) as acquired:
                self.assertFalse(acquired)
            waiting_done.set()
        waiting = self.spawn(waiter)
        shutdown, finished = self.begin_shutdown()
        self.assertTrue(waiting_done.wait(3))
        self.assertFalse(finished.is_set())
        release.set()
        self.join(operation, waiting, shutdown)

    def test_manual_operation_drains_and_skips_shutdown_notification(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        def switch(email, output):
            self.block(entered, release)
            output.ok = True
        with patch.object(self.sw, "_do_switch", side_effect=switch):
            operation = self.spawn(lambda: result.append(self.sw.switch_to_email("candidate@example.com")), release)
            self.assertTrue(entered.wait(3))
            shutdown, finished = self.begin_shutdown()
            self.assertFalse(finished.is_set())
            release.set()
            self.join(operation, shutdown)
        self.assertIn("退出", result[0].error)
        self.notify_switch.assert_not_called()
        self.record_switch.assert_not_called()

    def test_auto_loop_stops_before_second_candidate(self):
        entered, release = threading.Event(), threading.Event()
        self.pool.pick_next.return_value = {"email": "candidate@example.com"}
        def switch(email, output):
            self.block(entered, release)
            output.error = "offline failure"
        with patch.object(self.sw, "_do_switch", side_effect=switch):
            operation = self.spawn(self.sw.auto_switch, release)
            self.assertTrue(entered.wait(3))
            shutdown, finished = self.begin_shutdown()
            self.assertFalse(finished.is_set())
            release.set()
            self.join(operation, shutdown)
        self.pool.pick_next.assert_called_once()
        self.notify_switch.assert_not_called()

    def test_login_finishing_during_shutdown_does_not_fetch_subscription(self):
        entered, release = threading.Event(), threading.Event()
        cloud = Mock()
        cloud.login.side_effect = lambda: (self.block(entered, release), True)[1]
        with patch("switcher.CloudAccount", return_value=cloud):
            operation = self.spawn(lambda: self.sw.switch_to_email("candidate@example.com"), release)
            self.assertTrue(entered.wait(3))
            shutdown, _ = self.begin_shutdown()
            release.set()
            self.join(operation, shutdown)
        cloud.fetch_subscription.assert_not_called()
        self.engine.write_config.assert_not_called()
        self.pool.set_class_expire.assert_not_called()

    def test_written_config_during_shutdown_does_not_restart_engine(self):
        entered, release = threading.Event(), threading.Event()
        cloud = Mock(node={"id": "offline-id", "address": "node.example.com"}, class_expire=0)
        cloud.login.return_value = cloud.fetch_subscription.return_value = True
        self.engine.write_config.side_effect = lambda node: self.block(entered, release)
        with patch("switcher.CloudAccount", return_value=cloud):
            operation = self.spawn(lambda: self.sw.switch_to_email("candidate@example.com"), release)
            self.assertTrue(entered.wait(3))
            shutdown, _ = self.begin_shutdown()
            release.set()
            self.join(operation, shutdown)
        self.engine.start.assert_not_called()
        self.engine.check_direct_ip.assert_not_called()
        self.pool.set_active.assert_not_called()

    def test_failed_operation_releases_guard_and_shutdown_disables_restore(self):
        with patch.object(self.sw, "_do_switch", side_effect=RuntimeError("offline test")):
            result = self.sw.switch_to_email("failed@example.com", notify_fail=False)
        self.assertIn("异常", result.error)
        self.assertFalse(self.sw.is_switching())
        self.sw.begin_shutdown()
        self.sw._restore_link({"id": "offline-id"})
        self.engine.write_config.assert_not_called()

    def test_bootstrap_recovery_is_drained_before_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        cloud = Mock(node={"id": "offline-id"}, class_expire=0)
        cloud.login.return_value = cloud.fetch_subscription.return_value = True
        def write(node):
            self.assertTrue(self.sw.is_switching())
            self.block(entered, release)
        self.engine.write_config.side_effect = write
        with patch("cloud_api.CloudAccount", return_value=cloud):
            operation = self.spawn(lambda: _bootstrap(self.config, self.pool, self.engine,
                                                      self.sw, self.monitor, Mock()), release)
            self.assertTrue(entered.wait(3))
            shutdown, finished = self.begin_shutdown()
            self.assertFalse(finished.is_set())
            release.set()
            self.join(operation, shutdown)
        self.engine.start.assert_not_called()
        self.apply_if_enabled.assert_not_called()
        self.monitor.start.assert_not_called()

    def test_bootstrap_failure_switches_outside_runtime_guard(self):
        cloud = Mock()
        cloud.login.return_value = False
        cloud.login_error_code.return_value = "network"
        def auto(reason):
            self.assertFalse(self.sw.is_switching())
            return SwitchResult()
        with patch("cloud_api.CloudAccount", return_value=cloud), \
                patch.object(self.sw, "auto_switch", side_effect=auto) as switch:
            _bootstrap(self.config, self.pool, self.engine, self.sw, self.monitor, Mock())
        switch.assert_called_once_with("恢复失败，尝试换号")
        self.apply_if_enabled.assert_called_once()
        self.monitor.start.assert_called_once()

    def test_bootstrap_finalization_uses_runtime_guard(self):
        self.engine.is_running.return_value = True
        self.apply_if_enabled.side_effect = lambda *a, **k: self.assertTrue(self.sw.is_switching())
        self.monitor.start.side_effect = lambda: self.assertTrue(self.sw.is_switching())
        _bootstrap(self.config, self.pool, self.engine, self.sw, self.monitor, Mock())
        self.apply_if_enabled.assert_called_once()
        self.monitor.start.assert_called_once()
        self.assertFalse(self.sw.is_switching())

    def test_bootstrap_after_shutdown_does_nothing(self):
        self.sw.begin_shutdown()
        _bootstrap(self.config, self.pool, self.engine, self.sw, self.monitor, Mock())
        self.engine._discover.assert_not_called()
        self.pool.count.assert_not_called()
        self.apply_if_enabled.assert_not_called()
        self.monitor.start.assert_not_called()

    def test_bootstrap_shutdown_during_sysproxy_does_not_start_monitor(self):
        entered, release = threading.Event(), threading.Event()
        self.engine.is_running.return_value = True
        self.apply_if_enabled.side_effect = lambda *a, **k: self.block(entered, release)
        operation = self.spawn(lambda: _bootstrap(self.config, self.pool, self.engine,
                                                  self.sw, self.monitor, Mock()), release)
        self.assertTrue(entered.wait(3))
        shutdown, finished = self.begin_shutdown()
        self.assertFalse(finished.is_set())
        release.set()
        self.join(operation, shutdown)
        self.monitor.start.assert_not_called()

    def test_auto_rotation_still_tries_each_candidate_when_open(self):
        candidates = [{"email": f"rotation-{i}@example.com"} for i in range(3)]
        self.pool.pick_next.side_effect = candidates
        def fail(email, output):
            output.error = "offline candidate failure"
        with patch.object(self.sw, "_do_switch", side_effect=fail) as attempt:
            result = self.sw.auto_switch("offline rotation")
        self.assertEqual([call.args[0] for call in attempt.call_args_list],
                         [item["email"] for item in candidates])
        self.assertFalse(result.ok)
        self.assertEqual(self.record_switch.call_count, 3)
        self.notify_switch.assert_called_once()
        self.assertFalse(self.sw.is_switching())

    def test_existing_runtime_operation_still_rejects_switches(self):
        with self.sw.runtime_update_guard() as acquired:
            self.assertTrue(acquired)
            self.assertIn("已有切换", self.sw.auto_switch().error)
            self.assertIn("已有切换", self.sw.switch_to_email("candidate@example.com").error)
        self.pool.pick_next.assert_not_called()
        self.record_switch.assert_not_called()

    def test_runtime_exception_releases_busy_and_wakes_shutdown(self):
        entered, release = threading.Event(), threading.Event()
        def update():
            try:
                with self.sw.runtime_update_guard() as acquired:
                    self.assertTrue(acquired)
                    self.block(entered, release)
                    raise RuntimeError("offline update failure")
            except RuntimeError:
                pass
        operation = self.spawn(update, release)
        self.assertTrue(entered.wait(3))
        shutdown, finished = self.begin_shutdown()
        self.assertFalse(finished.is_set())
        release.set()
        self.join(operation, shutdown)
        self.assertTrue(finished.is_set())

    def test_bootstrap_normal_recovery_keeps_all_runtime_steps_guarded(self):
        cloud = Mock(node={"id": "offline-id"}, class_expire=0)
        cloud.login.return_value = cloud.fetch_subscription.return_value = True
        self.engine.write_config.side_effect = lambda node: self.assertTrue(self.sw.is_switching())
        def start():
            self.assertTrue(self.sw.is_switching())
            return True
        self.engine.start.side_effect = start
        self.apply_if_enabled.side_effect = lambda *a, **k: self.assertTrue(self.sw.is_switching())
        self.monitor.start.side_effect = lambda: self.assertTrue(self.sw.is_switching())
        with patch("cloud_api.CloudAccount", return_value=cloud):
            _bootstrap(self.config, self.pool, self.engine, self.sw, self.monitor, Mock())
        self.engine.write_config.assert_called_once_with(cloud.node)
        self.engine.start.assert_called_once()
        self.monitor.start.assert_called_once()
        self.assertFalse(self.sw.is_switching())


if __name__ == "__main__":
    unittest.main()
