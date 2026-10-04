"""只运行本机 v2ray -test，临时配置不监听端口、不访问网络。"""
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config
from v2ray_engine import V2RayEngine


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    engine_path = os.path.join(root, "v2ray_work", "v2ray.exe")
    if not os.path.isfile(engine_path):
        raise SystemExit("没有本机 v2ray，未执行引擎配置校验")
    with tempfile.TemporaryDirectory(prefix="misty_config_check_") as folder:
        config = Config(path=os.path.join(folder, "settings.json"))
        config.set("proxy_domain_rules", [
            {"domain": "qq.com", "action": "proxy"},
            {"domain": "school.example.com", "action": "direct"},
            {"domain": "download.qq.com", "action": "direct"},
        ])
        engine = V2RayEngine.__new__(V2RayEngine)
        engine.config = config
        engine.work_dir = folder
        node = {"address": "node.example.com", "port": 443,
                "id": "00000000-0000-4000-8000-000000000001", "aid": 0}
        generated = engine._fallback_config(node, 10808)
        variants = [("fallback", generated)]
        template = json.loads(json.dumps(generated))
        for outbound in template["outbounds"]:
            if outbound["protocol"] == "vmess":
                old_tag = outbound["tag"]
                outbound["tag"] = "custom-line"
                for rule in template["routing"]["rules"]:
                    if rule.get("outboundTag") == old_tag:
                        rule["outboundTag"] = "custom-line"
        engine._apply_bypass_routing(template)
        variants.append(("template_with_custom_tag", template))
        for name, data in variants:
            filename = os.path.join(folder, name + ".json")
            with open(filename, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            result = subprocess.run([engine_path, "-test", "-config", filename],
                                    capture_output=True, text=True, timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise AssertionError(f"{name}: {result.stdout} {result.stderr}")
            print(f"{name}: v2ray 配置校验通过")
        print("2 份临时配置均通过；未启动代理监听或目标站请求")


if __name__ == "__main__":
    main()
