"""离线诊断测试：所有系统采集均替换为假数据，日志仅使用临时目录。"""
import base64
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxy_diagnostics as diagnostics


class ProxyDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = SimpleNamespace(port=10808, http_port=10809, work_dir=self.directory.name)
        self.proxy_state = {"status": "observed", "enable": 1,
                            "server": "http=127.0.0.1:10809;https=127.0.0.1:10809",
                            "pac": "", "override": "<local>;*.example.cn"}
        self.addCleanup(patch.stopall)
        patch.object(diagnostics, "_local_now", return_value=dt.datetime(2026, 10, 3, 13)).start()
        patch.object(diagnostics, "_read_system_proxy", return_value=self.proxy_state).start()
        patch.object(diagnostics, "_collect_connections", return_value={
            "status": "observed", "processes": [], "connections": []}).start()

    def write_log(self, lines):
        Path(self.directory.name, "access.log").write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def connection(pid, address, port, state="Established"):
        return {"OwningProcess": pid, "RemoteAddress": address, "RemotePort": port,
                "LocalAddress": "127.0.0.1", "LocalPort": 54000, "State": state}

    def test_current_ports_merge_electron_processes_and_do_not_deduce_direct(self):
        snapshot = {"status": "observed", "processes": [
            {"Id": 101, "ProcessName": "Claude"}, {"Id": 102, "ProcessName": "Claude"},
            {"Id": 201, "ProcessName": "Antigravity"}], "connections": [
                self.connection(101, "127.0.0.1", 10809),
                self.connection(102, "::ffff:127.0.0.1", 10808),
                self.connection(102, "203.0.113.10", 443),
                self.connection(201, "203.0.113.20", 443),
                self.connection(201, "::1", 9000),
                self.connection(201, "127.0.0.1", 10809, "TimeWait"),
                self.connection(999, "127.0.0.1", 10809)]}
        with patch.object(diagnostics, "_collect_connections", return_value=snapshot):
            result = diagnostics.build_diagnostics({}, self.engine)
        claude, antigravity = result["applications"]["items"]
        self.assertEqual(claude["status"], "observed")
        self.assertEqual(claude["pids"], [101, 102])
        self.assertEqual(claude["tool_proxy_connection_count"], 2)
        self.assertEqual(claude["other_external_connections"], 1)
        self.assertEqual(antigravity["status"], "no_current_evidence")
        self.assertEqual(antigravity["other_external_connections"], 1)
        self.assertEqual(antigravity["other_loopback_connections"], 1)
        self.assertIn("不等于", antigravity["message"])
        self.assertNotIn("203.0.113", json.dumps(result))
        self.assertTrue(any("登录请求" in line for line in result["limitations"]))

    def test_access_log_reports_routing_only_and_removes_sensitive_details(self):
        self.write_log([
            "2026/10/03 12:00:00 127.0.0.1:500 accepted tcp:example.com:443 [http -> proxy] email=someone@example.com token=SECRET",
            "2026/10/03 12:00:01 127.0.0.1:501 accepted tcp:example.cn:80 [proxy -> direct]",
            "2026/10/03 12:00:02 127.0.0.1:501 accepted tcp:misty.example:443 [http -> misty]",
            "2026/10/03 12:00:03 127.0.0.1:502 accepted tcp:127.0.0.1:10085 [api -> api]",
            "2026/10/03 12:00:04 127.0.0.1:503 accepted tcp:unknown.example:443 [http -> custom]",
            "2026/10/03 12:00:05 127.0.0.1:504 accepted tcp:user:SECRET@secret.example:443 [http -> proxy]",
            "unexpected raw password=SECRET",
        ])
        result = diagnostics.build_diagnostics({}, self.engine, include_connections=False)
        access = result["engine_access"]
        self.assertEqual(access["status"], "observed")
        self.assertEqual([record["action"] for record in access["records"]],
                         ["unknown", "proxy", "direct", "proxy"])
        self.assertTrue(all("age_seconds" in record for record in access["records"]))
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("someone@", json.dumps(result))
        self.assertNotIn("api", [record["inbound"] for record in access["records"]])
        self.assertIn("不代表请求完成", access["message"])

    def test_log_absent_and_empty_have_different_unknown_states(self):
        self.assertEqual(diagnostics._access_evidence(self.engine)["status"], "unknown")
        self.write_log([])
        self.assertEqual(diagnostics._access_evidence(self.engine)["status"], "no_current_evidence")
        self.engine.work_dir = ""
        self.assertEqual(diagnostics._access_evidence(self.engine)["status"], "unknown")

    def test_legacy_v2ray_access_records_support_connect_and_outbound_only(self):
        self.write_log([
            "2026/10/03 12:00:00 127.0.0.1:500 accepted //example.com:443 [misty]",
            "2026/10/03 12:00:01 127.0.0.1:501 accepted tcp:example.cn:80 [direct]",
            "2026/10/03 12:00:02 127.0.0.1:502 accepted udp:dns.example:53 [proxy >> misty]",
        ])
        evidence = diagnostics._access_evidence(self.engine)
        self.assertEqual(evidence["status"], "observed")
        self.assertEqual([r["action"] for r in evidence["records"]], ["proxy", "direct", "proxy"])
        self.assertEqual([r["inbound"] for r in evidence["records"]], ["proxy", "", ""])
        self.assertEqual(evidence["records"][-1]["domain"], "example.com")

    def test_legacy_access_records_still_reject_credentials_and_hide_ip_targets(self):
        self.write_log([
            "2026/10/03 12:00:00 127.0.0.1:500 accepted //user:SECRET@secret.example:443 [misty]",
            "2026/10/03 12:00:01 127.0.0.1:501 accepted https://secret.example/path?token=SECRET [misty]",
            "2026/10/03 12:00:02 127.0.0.1:502 accepted //203.0.113.10:443 [misty] email=private@example.com",
            "2026/10/03 12:00:03 127.0.0.1:503 accepted tcp:127.0.0.1:10085 [api]",
        ])
        evidence = diagnostics._access_evidence(self.engine)
        self.assertEqual(len(evidence["records"]), 1)
        self.assertEqual(evidence["records"][0]["destination_type"], "ip")
        self.assertNotIn("SECRET", json.dumps(evidence))
        self.assertNotIn("private@", json.dumps(evidence))
        self.assertNotIn("203.0.113", json.dumps(evidence))

    def test_log_tail_returns_latest_twenty_records_and_bounded_read(self):
        self.write_log(["X" * 100000] + [
            f"2026/10/03 12:00:{i:02d} 127.0.0.1:500 accepted tcp:site{i}.example:443 [http -> proxy]"
            for i in range(40)])
        result = diagnostics._access_evidence(self.engine)
        self.assertEqual(len(result["records"]), 20)
        self.assertEqual(result["records"][0]["domain"], "site39.example")
        self.assertEqual(result["records"][-1]["domain"], "site20.example")

    def test_system_proxy_is_read_fact_and_pac_is_not_reported_as_success(self):
        self.proxy_state["pac"] = "https://user:SECRET@pac.example/SECRET"
        result = diagnostics.build_diagnostics({"system_proxy": False}, self.engine)
        proxy = result["system_proxy"]
        self.assertEqual(proxy["status"], "observed")
        self.assertTrue(proxy["enabled"])
        self.assertTrue(proxy["http_points_to_engine"])
        self.assertTrue(proxy["pac_configured"])
        self.assertIn("未验证", proxy["message"])
        self.assertNotIn("SECRET", json.dumps(result))
        self.proxy_state["status"] = "unknown"
        self.assertEqual(diagnostics.build_diagnostics({}, self.engine)["system_proxy"]["status"], "unknown")

    def test_static_shell_is_hidden_bounded_and_never_interpolates_query(self):
        fake_result = SimpleNamespace(returncode=0, stdout=json.dumps({"processes": [], "connections": []}))
        with patch.object(diagnostics.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
             patch.object(diagnostics.subprocess, "run", return_value=fake_result) as runner:
            diagnostics._run_powershell(diagnostics._CONNECTION_SCRIPT)
        args, kwargs = runner.call_args
        command = args[0]
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertEqual(script, diagnostics._CONNECTION_SCRIPT)
        self.assertEqual(kwargs["timeout"], 8)
        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertFalse(kwargs.get("shell", False))
        self.assertIn("Get-NetTCPConnection", script)
        self.assertIn("[string]$_.State", script)
        self.assertNotIn("CommandLine", script)
        self.assertNotIn("Path", script)
        for query in ("Claude; Remove-Item x", "$(Write-Output SECRET)", "Claude`nwhoami"):
            with patch.object(diagnostics, "_collect_connections") as collector:
                result = diagnostics.build_diagnostics({}, self.engine, app_query=query)
            self.assertEqual(result["applications"]["status"], "invalid_query")
            collector.assert_not_called()
            self.assertNotIn(query, json.dumps(result))

    def test_timeout_failed_snapshot_and_disabled_collection_are_unknown(self):
        # 调用原实现，不使用 setUp 中覆盖的采集器。
        with patch.object(diagnostics.sys, "platform", "win32"), \
             patch.object(diagnostics, "_run_powershell", side_effect=subprocess.TimeoutExpired("hidden", 8)):
            result = ORIGINAL_COLLECT()
        self.assertEqual(result, {"status": "unknown", "reason": "timeout"})
        with patch.object(diagnostics, "_collect_connections", return_value=result):
            built = diagnostics.build_diagnostics({}, self.engine)
        self.assertEqual(built["applications"]["status"], "unknown")
        self.assertEqual(built["applications"]["items"], [])
        with patch.object(diagnostics, "_collect_connections") as collector:
            disabled = diagnostics.build_diagnostics({}, self.engine, include_connections=False)
        collector.assert_not_called()
        self.assertEqual(disabled["applications"]["status"], "not_requested")

    def test_domain_rule_remains_expectation_and_log_filter_does_not_match_unrelated_domain(self):
        fake_rules = SimpleNamespace(normalize_domain=lambda value: "example.com",
                                    resolve_domain=lambda config, domain: {
            "domain": "example.com", "action": "proxy", "source": "default", "matched_domain": ""})
        self.write_log([
            "2026/10/03 12:00:00 127.0.0.1:500 accepted tcp:example.com:443 [http -> direct]",
            "2026/10/03 12:00:01 127.0.0.1:501 accepted tcp:other.example:443 [http -> proxy]",
        ])
        with patch.dict(sys.modules, {"routing_rules": fake_rules}):
            result = diagnostics.build_diagnostics({}, self.engine, domain="example.com")
        self.assertEqual(result["rule_expectation"]["status"], "expected")
        self.assertEqual(result["rule_expectation"]["action"], "proxy")
        self.assertEqual(len(result["engine_access"]["records"]), 1)
        self.assertEqual(result["engine_access"]["records"][0]["action"], "direct")
        self.assertNotIn("actual", result["rule_expectation"])

    def test_invalid_domain_never_leaks_query_or_queries_log(self):
        fake_rules = SimpleNamespace(normalize_domain=lambda value: value,
                                    resolve_domain=lambda config, domain: {
            "domain": domain, "action": "unknown", "source": "invalid", "matched_domain": ""})
        with patch.dict(sys.modules, {"routing_rules": fake_rules}), \
             patch.object(diagnostics, "_access_evidence") as logs:
            result = diagnostics.build_diagnostics({}, self.engine, domain="SECRET@example.com")
        self.assertEqual(result["rule_expectation"]["status"], "invalid_domain")
        logs.assert_not_called()
        self.assertNotIn("SECRET", json.dumps(result))

    def test_numeric_state_five_is_established_and_other_numeric_states_are_ignored(self):
        snapshot = {"status": "observed", "processes": [{"Id": 101, "ProcessName": "Claude"}],
                    "connections": [self.connection(101, "127.0.0.1", 10809, 5),
                                    self.connection(101, "::1", 10808, "5"),
                                    self.connection(101, "127.0.0.1", 10809, 4)]}
        result = diagnostics._application_evidence(snapshot, ["Claude"], {"socks": 10808, "http": 10809})
        self.assertEqual(result["items"][0]["status"], "observed")
        self.assertEqual(result["items"][0]["tool_proxy_connection_count"], 2)

    def test_listener_owned_by_running_engine_verifies_both_string_and_numeric_listen(self):
        self.engine.proc = SimpleNamespace(pid=999, poll=lambda: None)
        snapshot = {"status": "observed", "processes": [{"Id": 101, "ProcessName": "Claude"}],
                    "connections": [self.connection(101, "127.0.0.1", 10809),
                                    self.connection(101, "::1", 10808),
                                    {"OwningProcess": 999, "State": "Listen", "LocalPort": 10809,
                                     "LocalAddress": "0.0.0.0"},
                                    {"OwningProcess": 999, "State": 2, "LocalPort": 10808,
                                     "LocalAddress": "::"}]}
        with patch.object(diagnostics, "_collect_connections", return_value=snapshot):
            result = diagnostics.build_diagnostics({}, self.engine, app_query="Claude")
        app = result["applications"]["items"][0]
        self.assertTrue(app["listener_verified"])
        self.assertTrue(all(conn["listener_verified"] for conn in app["tool_proxy_connections"]))
        self.assertIn("已核验本引擎", app["message"])

    def test_other_process_listener_and_uncovered_address_do_not_verify_engine(self):
        self.engine.proc = SimpleNamespace(pid=999, poll=lambda: None)
        snapshot = {"status": "observed", "processes": [{"Id": 101, "ProcessName": "Claude"}],
                    "connections": [self.connection(101, "127.0.0.1", 10809),
                                    self.connection(101, "::1", 10808),
                                    {"OwningProcess": 998, "State": "Listen", "LocalPort": 10809,
                                     "LocalAddress": "127.0.0.1"},
                                    {"OwningProcess": 999, "State": 2, "LocalPort": 10808,
                                     "LocalAddress": "127.0.0.1"}]}
        app = diagnostics._application_evidence(snapshot, ["Claude"], {"socks": 10808, "http": 10809}, self.engine)["items"][0]
        self.assertEqual(app["status"], "observed")
        self.assertFalse(app["listener_verified"])
        self.assertTrue(all(not conn["listener_verified"] for conn in app["tool_proxy_connections"]))
        self.assertIn("监听者未核验", app["message"])

    def test_missing_pid_missing_listener_and_exited_process_never_verify(self):
        base = {"status": "observed", "processes": [{"Id": 101, "ProcessName": "Claude"}],
                "connections": [self.connection(101, "127.0.0.1", 10809)]}
        listener = {"OwningProcess": 999, "State": "Listen", "LocalPort": 10809,
                    "LocalAddress": "127.0.0.1"}
        cases = [(None, True), (SimpleNamespace(pid=None), True),
                 (SimpleNamespace(pid=999), False), (SimpleNamespace(pid=999, poll=lambda: 0), True)]
        for proc, include_listener in cases:
            with self.subTest(proc=proc):
                self.engine.proc = proc
                snapshot = {**base, "connections": base["connections"] + ([listener] if include_listener else [])}
                app = diagnostics._application_evidence(snapshot, ["Claude"], {"socks": 10808, "http": 10809}, self.engine)["items"][0]
                self.assertFalse(app["listener_verified"])
                self.assertFalse(app["tool_proxy_connections"][0]["listener_verified"])

    def test_partial_listener_verification_keeps_whole_app_unverified(self):
        self.engine.proc = SimpleNamespace(pid=999)
        snapshot = {"status": "observed", "processes": [{"Id": 101, "ProcessName": "Claude"}],
                    "connections": [self.connection(101, "127.0.0.1", 10809),
                                    self.connection(101, "::1", 10808),
                                    {"OwningProcess": 999, "State": 2, "LocalPort": 10809,
                                     "LocalAddress": "127.0.0.1"}]}
        app = diagnostics._application_evidence(snapshot, ["Claude"], {"socks": 10808, "http": 10809}, self.engine)["items"][0]
        self.assertFalse(app["listener_verified"])
        self.assertEqual([conn["listener_verified"] for conn in app["tool_proxy_connections"]], [True, False])

    def test_unmatched_rule_is_unknown_but_engine_log_independently_reports_proxy(self):
        self.write_log(["2026/10/03 12:00:00 127.0.0.1:500 accepted tcp:www.example.com:443 [http -> proxy]"])
        result = diagnostics.build_diagnostics({}, self.engine, domain="example.com", include_connections=False)
        rule = result["rule_expectation"]
        self.assertEqual(rule["status"], "unknown")
        self.assertEqual(rule["action"], "unknown")
        self.assertEqual(rule["source"], "template")
        self.assertIn("由引擎模板决定", rule["message"])
        self.assertEqual(result["engine_access"]["status"], "observed")
        self.assertEqual(result["engine_access"]["records"][0]["action"], "proxy")

    def test_system_proxy_message_describes_actual_enable_target_and_domain_bypass(self):
        ports = {"http": 10809, "socks": 10808}
        enabled = diagnostics._system_evidence(self.proxy_state, ports, "www.example.cn")
        self.assertIn("手动系统代理已启用", enabled["message"])
        self.assertIn("HTTP 代理指向本工具", enabled["message"])
        self.assertIn("HTTPS 代理指向本工具", enabled["message"])
        self.assertIn("目标域名命中系统代理例外", enabled["message"])
        self.assertTrue(enabled["https_points_to_engine"])
        self.proxy_state.update(enable=0, server="http=127.0.0.1:9000;https=127.0.0.1:10809")
        disabled = diagnostics._system_evidence(self.proxy_state, ports, "other.example")
        self.assertIn("手动系统代理未启用", disabled["message"])
        self.assertIn("HTTP 代理未指向本工具", disabled["message"])
        self.assertIn("目标域名未命中系统代理例外", disabled["message"])
        self.assertFalse(disabled["http_points_to_engine"])
        self.assertTrue(disabled["https_points_to_engine"])

    def test_log_domain_filter_includes_subdomains_before_final_twenty_unrelated_lines(self):
        matching = [
            f"2026/10/03 12:00:{i:02d} 127.0.0.1:500 accepted tcp:sub{i}.claude.ai:443 [http -> proxy]"
            for i in range(25)]
        unrelated = [
            f"2026/10/03 12:01:{i:02d} 127.0.0.1:500 accepted tcp:notclaude.ai:443 [http -> proxy]"
            for i in range(30)]
        self.write_log(matching + unrelated)
        result = diagnostics._access_evidence(self.engine, "claude.ai")
        self.assertEqual(len(result["records"]), 20)
        self.assertEqual(result["records"][0]["domain"], "sub24.claude.ai")
        self.assertEqual(result["records"][-1]["domain"], "sub5.claude.ai")
        self.assertNotIn("notclaude.ai", json.dumps(result))

    def test_outbound_metadata_maps_custom_tags_without_reading_config_json(self):
        self.engine.outbound_actions = {"actual-node": "proxy", "lan": "direct",
                                        "reject": "block", "direct": "proxy", "alien": "unknown"}
        self.write_log([
            f"2026/10/03 12:00:{i:02d} 127.0.0.1:500 accepted tcp:example.com:443 [http -> {tag}]"
            for i, tag in enumerate(["actual-node", "lan", "reject", "direct", "alien"])])
        Path(self.directory.name, "config.json").write_text("SECRET_NOT_JSON", encoding="utf-8")
        evidence = diagnostics._access_evidence(self.engine)
        actions = {record["outbound"]: record["action"] for record in evidence["records"]}
        self.assertEqual(actions, self.engine.outbound_actions)
        self.assertNotIn("SECRET", json.dumps(evidence))

    def test_future_log_timestamp_is_unknown_and_ip_is_not_disclosed(self):
        self.write_log([
            "2026/10/03 14:00:00 127.0.0.1:500 accepted tcp:203.0.113.10:443 [http -> proxy]",
            "2026/10/03 14:00:01 127.0.0.1:501 accepted tcp:[2001:db8::1]:443 [http -> proxy]",
        ])
        evidence = diagnostics._access_evidence(self.engine)
        self.assertEqual(evidence["status"], "unknown")
        self.assertTrue(all(r["time_status"] == "unknown" and r["age_seconds"] is None
                            for r in evidence["records"]))
        self.assertNotIn("203.0.113", json.dumps(evidence))
        self.assertNotIn("2001:db8", json.dumps(evidence))

    def test_actual_routing_api_normalizes_url_without_exposing_path_or_token(self):
        result = diagnostics.build_diagnostics(
            {"proxy_domain_rules": [{"domain": "example.com", "action": "direct"}]}, self.engine,
            domain="https://Sub.Example.com/login?token=SECRET", include_connections=False)
        rule = result["rule_expectation"]
        self.assertEqual(rule["status"], "expected")
        self.assertEqual(rule["domain"], "sub.example.com")
        self.assertEqual(rule["action"], "direct")
        self.assertEqual(rule["matched_domain"], "example.com")
        self.assertNotIn("SECRET", json.dumps(result))


ORIGINAL_COLLECT = diagnostics._collect_connections

if __name__ == "__main__":
    unittest.main()
