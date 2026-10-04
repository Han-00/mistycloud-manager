"""网站规则离线测试：临时配置/模板、假引擎/注册表，不启动进程或联网。"""
import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config, DEFAULTS, get_effective_bypass
from routing_rules import (normalize_domain, normalize_rules, effective_bypass_domains,
                           resolve_domain, get_rule_state)
from v2ray_engine import V2RayEngine
import sysproxy

NODE = {"address": "node.example.com", "port": 443, "id": "offline-test-id", "aid": 0}


def engine(data):
    result = V2RayEngine.__new__(V2RayEngine)
    result.config = data
    return result


class RoutingTests(unittest.TestCase):
    def test_normalize_domain(self):
        for value in ("QQ.COM", "*.qq.com", "qq.com.", "https://QQ.COM:443/path?q=x"):
            self.assertEqual(normalize_domain(value), "qq.com")
        self.assertEqual(normalize_domain("https://例子.中国/"), "xn--fsqu00a.xn--fiqs8s")

    def test_reject_invalid_domains(self):
        for value in (None, 12, "", "localhost", "evil;*.qq.com", "qq.com/path", "qq.com:443",
                      "a..com", "-a.com", "a-.com", "a_b.com", "qq.*", ".qq.com", "127.0.0.1",
                      "https://[::1]/", "https://qq.com:bad/", "https://user:pass@qq.com/",
                      "ftp://qq.com/", "q q.com", "https://qq.com/\npath", "a" * 64 + ".com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_domain(value)

    def test_normalize_rules_strict_and_duplicates(self):
        self.assertEqual(normalize_rules([{"domain": "*.QQ.COM", "action": "direct"},
                                          {"domain": "qq.com", "action": "proxy"}]),
                         [{"domain": "qq.com", "action": "proxy"}])
        for raw in (None, {}, ["qq.com"], [{"domain": "qq.com", "action": "block"}],
                    [{"domain": "qq.*", "action": "direct"}]):
            with self.assertRaises(ValueError):
                normalize_rules(raw)

    def test_user_overrides_and_specificity(self):
        data = {"proxy_bypass_domains": ["qq.com"], "proxy_domain_rules": [
            {"domain": "qq.com", "action": "proxy"},
            {"domain": "work.qq.com", "action": "direct"},
            {"domain": "private.work.qq.com", "action": "proxy"}]}
        self.assertEqual(resolve_domain(data, "qq.com"),
                         {"action": "proxy", "source": "user", "matched_domain": "qq.com"})
        self.assertEqual(resolve_domain(data, "cdn.work.qq.com")["action"], "direct")
        self.assertEqual(resolve_domain(data, "private.work.qq.com")["action"], "proxy")
        self.assertEqual(effective_bypass_domains(data), [])

    def test_proxy_subdomain_removes_system_parent_only(self):
        data = {"proxy_bypass_domains": ["qq.com", "bilibili.com"], "proxy_domain_rules": [
            {"domain": "video.qq.com", "action": "proxy"}]}
        self.assertEqual(effective_bypass_domains(data), ["bilibili.com"])
        self.assertEqual(resolve_domain(data, "mail.qq.com")["action"], "direct")
        self.assertEqual(resolve_domain(data, "cdn.video.qq.com")["action"], "proxy")
        rules = engine(data)._fallback_config(NODE, 10808)["routing"]["rules"]
        self.assertEqual(rules[0]["domain"], ["domain:video.qq.com"])
        self.assertIn("domain:qq.com", rules[1]["domain"])

    def test_direct_subdomain_overrides_proxy_parent(self):
        data = {"proxy_bypass_domains": ["qq.com"], "proxy_domain_rules": [
            {"domain": "qq.com", "action": "proxy"}, {"domain": "mail.qq.com", "action": "direct"}]}
        self.assertEqual(effective_bypass_domains(data), ["mail.qq.com"])
        rules = engine(data)._fallback_config(NODE, 10808)["routing"]["rules"]
        self.assertEqual([r["outboundTag"] for r in rules[:2]], ["direct", "proxy"])

    def test_suffix_boundaries_and_fake_config(self):
        data = {"proxy_bypass_domains": ["qq.com"]}
        self.assertEqual(resolve_domain(data, "evilqq.com"),
                         {"action": "unknown", "source": "template", "matched_domain": None})
        self.assertEqual(resolve_domain(data, "qq.com.evil.com")["action"], "unknown")
        self.assertEqual(resolve_domain(data, "qq.com.evil.com")["source"], "template")
        self.assertEqual(resolve_domain(data, "cdn.qq.com")["source"], "builtin")
        self.assertEqual(get_effective_bypass({}), [])
        self.assertEqual(resolve_domain({}, "qq.com")["source"], "template")
        # 兜底模板内置此域 direct；配置层解析必须避免误报为 proxy。
        self.assertEqual(resolve_domain({}, "mistycapsule.xyz"),
                         {"action": "unknown", "source": "template", "matched_domain": None})

    def test_old_settings_and_new_key_survive_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"proxy_bypass_domains": ["legacy.example.com", "*.QQ.COM"],
                           "proxy_domain_rules": [{"domain": "qq.com", "action": "proxy"}],
                           "extra_field": "keep"}, handle)
            cfg = Config(path)
            self.assertIn("bilibili.com", cfg.get("proxy_bypass_domains"))
            self.assertNotIn("qq.com", get_effective_bypass(cfg))
            self.assertIn("legacy.example.com", get_effective_bypass(cfg))
            self.assertTrue(cfg.save())
            saved = Config(path)
            self.assertEqual(saved.get("extra_field"), "keep")
            self.assertEqual(saved.get("proxy_domain_rules"), cfg.get("proxy_domain_rules"))
            self.assertEqual(get_rule_state(saved)["legacy_direct_domains"], ["legacy.example.com"])

    def test_default_lists_are_independent_and_save_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(os.path.join(directory, "missing.json"))
            cfg.get("proxy_domain_rules").append({"domain": "qq.com", "action": "proxy"})
            self.assertEqual(DEFAULTS["proxy_domain_rules"], [])
            cfg.path = os.path.join(directory, "missing-dir", "settings.json")
            self.assertFalse(cfg.save())

    def test_serialized_save(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(os.path.join(directory, "settings.json"))
            results = []
            threads = [threading.Thread(target=lambda: results.append(cfg.save())) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(results, [True] * 8)
            with open(cfg.path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["proxy_domain_rules"], [])

    def test_corrupt_stored_items_are_ignored(self):
        data = {"proxy_bypass_domains": ["qq.com", {}, "qq.*"], "proxy_domain_rules": [
            {"domain": "qq.*", "action": "proxy"}, {"domain": "qq.com", "action": "proxy"}, None]}
        self.assertEqual(effective_bypass_domains(data), [])
        self.assertEqual(get_rule_state(data)["rules"], [{"domain": "qq.com", "action": "proxy"}])

    def test_template_tag_and_idempotence(self):
        data = {"proxy_bypass_domains": ["qq.com"], "proxy_domain_rules": [
            {"domain": "video.qq.com", "action": "proxy"}]}
        eng = engine(data)
        with tempfile.TemporaryDirectory() as directory:
            eng.v2ray_source_dir = directory
            eng.work_dir = directory
            template = {"inbounds": [{"protocol": "socks", "port": 10808}],
                        "outbounds": [{"tag": "misty", "protocol": "vmess"},
                                      {"tag": "bypass", "protocol": "freedom"}],
                        "routing": {"rules": [{"type": "field", "outboundTag": "misty", "network": "tcp,udp"}]},
                        "log": {"access": "Z:/remote/access.log"}}
            with open(os.path.join(directory, "config.json"), "w", encoding="utf-8") as handle:
                json.dump(template, handle)
            cfg = eng._build_config(NODE, 10808)
            self.assertEqual(cfg["routing"]["rules"][0]["outboundTag"], "misty")
            self.assertEqual(cfg["routing"]["rules"][1]["outboundTag"], "bypass")
            tags = {o["tag"] for o in cfg["outbounds"]}
            self.assertTrue(all(r.get("outboundTag") in tags for r in cfg["routing"]["rules"]))
            previous = copy.deepcopy(cfg)
            eng._apply_bypass_routing(cfg)
            self.assertEqual(cfg, previous)
            self.assertEqual(eng._build_config(NODE, 10808), previous)
            serialized = json.loads(json.dumps(cfg))
            eng._apply_bypass_routing(serialized)
            self.assertEqual(serialized, previous)
            self.assertEqual(cfg["log"]["access"], os.path.join(directory, "access.log"))
            self.assertEqual(cfg["log"]["loglevel"], "warning")
            self.assertFalse(os.path.exists(os.path.join(directory, "access.log")))
            data["proxy_domain_rules"] = []
            eng._apply_bypass_routing(cfg)
            self.assertEqual(cfg["routing"]["rules"][0]["domain"], ["domain:qq.com"])
            self.assertEqual(len(cfg["routing"]["rules"]), 2)

    def test_sysproxy_bypass_is_safe(self):
        text = sysproxy._bypass_string(["https://QQ.COM/path", "qq.*", "qq.com;evil.com", None])
        self.assertEqual(text, sysproxy.OVERRIDE_DEFAULT + ";*.qq.com;qq.com")

    def test_write_config_records_actual_outbound_tags(self):
        eng = engine({})
        with tempfile.TemporaryDirectory() as directory:
            eng.work_dir = directory
            eng.port = 10808
            cfg = {"inbounds": [{"protocol": "http", "port": 10809}], "outbounds": [
                {"tag": "misty", "protocol": "vmess"}, {"tag": "free", "protocol": "freedom"},
                {"tag": "reject", "protocol": "blackhole"}, {"tag": "api", "protocol": "freedom"},
                {"tag": "opaque", "protocol": "custom"}]}
            with patch.object(eng, "_build_config", return_value=cfg):
                eng.write_config(NODE)
            self.assertEqual(eng.outbound_actions,
                             {"misty": "proxy", "free": "direct", "reject": "block", "opaque": "unknown"})

    def test_start_rotates_large_log_after_stop_and_keeps_one_backup(self):
        eng = engine({})
        with tempfile.TemporaryDirectory() as directory:
            eng.work_dir = directory
            eng.port = 10808
            Path(directory, "config.json").write_text("{}", encoding="utf-8")
            access = Path(directory, "access.log")
            previous = Path(directory, "access.previous.log")
            with access.open("wb") as handle:
                handle.truncate(2 * 1024 * 1024 + 1)
            previous.write_bytes(b"old synthetic backup")
            stopped = []
            def stop():
                stopped.append(True)
            def launch(*args, **kwargs):
                self.assertTrue(stopped)
                self.assertFalse(access.exists())
                self.assertEqual(previous.stat().st_size, 2 * 1024 * 1024 + 1)
                return object()
            with patch.object(eng, "stop", side_effect=stop), \
                    patch.object(eng, "prepare", return_value=True), \
                    patch("v2ray_engine.subprocess.Popen", side_effect=launch), \
                    patch("v2ray_engine.subprocess.CREATE_NO_WINDOW", 0, create=True):
                self.assertTrue(eng.start())
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),
                             ["access.previous.log", "config.json"])

    def test_start_continues_if_rotation_fails_and_keeps_small_log(self):
        eng = engine({})
        with tempfile.TemporaryDirectory() as directory:
            eng.work_dir = directory
            eng.port = 10808
            Path(directory, "config.json").write_text("{}", encoding="utf-8")
            access = Path(directory, "access.log")
            access.write_bytes(b"synthetic small log")
            eng._rotate_access_log()
            self.assertTrue(access.exists())
            self.assertFalse(Path(directory, "access.previous.log").exists())
            with access.open("wb") as handle:
                handle.truncate(2 * 1024 * 1024 + 1)
            with patch.object(eng, "stop"), patch.object(eng, "prepare", return_value=True), \
                    patch("v2ray_engine.os.replace", side_effect=PermissionError("offline fake failure")), \
                    patch("v2ray_engine.subprocess.Popen", return_value=object()) as launch, \
                    patch("v2ray_engine.subprocess.CREATE_NO_WINDOW", 0, create=True):
                self.assertTrue(eng.start())
                launch.assert_called_once()
            self.assertTrue(access.exists())

    def test_rule_change_reconciles_fake_registry(self):
        registry = {}
        class FakeEngine:
            http_port = 10809
            def is_running(self):
                return True
            def _port_open(self, port):
                return True
        data = {"system_proxy": True, "proxy_bypass_domains": ["qq.com", "bilibili.com"]}
        with patch.object(sysproxy, "available", return_value=True), \
                patch.object(sysproxy, "_reg_read", side_effect=lambda k, n: registry.get((k, n))), \
                patch.object(sysproxy, "_reg_write", side_effect=lambda k, n, v, kind="sz": registry.__setitem__((k, n), v)), \
                patch.object(sysproxy, "_reg_delete", side_effect=lambda k, n: registry.pop((k, n), None)), \
                patch.object(sysproxy, "_notify"):
            self.assertTrue(sysproxy.apply_if_enabled(data, FakeEngine()))
            self.assertIn("*.qq.com", registry[(sysproxy._INET_KEY, "ProxyOverride")])
            data["proxy_domain_rules"] = [{"domain": "video.qq.com", "action": "proxy"}]
            self.assertTrue(sysproxy.apply_if_enabled(data, FakeEngine()))
            self.assertEqual(registry[(sysproxy._INET_KEY, "ProxyOverride")],
                             sysproxy.OVERRIDE_DEFAULT + ";*.bilibili.com;bilibili.com")


if __name__ == "__main__":
    unittest.main()
