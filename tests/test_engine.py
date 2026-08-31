# -*- coding: utf-8 -*-
"""引擎模块自测：发现 v2ray → 构建配置 → 启动 → 端口 → 出口 IP → 停止。"""
import io
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

from config import Config
from v2ray_engine import V2RayEngine

NODE = {
    "address": "210.13.85.170",
    "port": 24450,
    "id": "2650f128-d62f-38f9-827d-b2110860b04f",
    "aid": 2,
    "security": "auto",
    "network": "ws",
    "ws_path": "/orbitrelay",
    "ws_host": "jp3.orbitdns.xyz",
    "remarks": "template-node",
    "tls": "",
}

def main():
    cfg = Config()
    eng = V2RayEngine(cfg)
    print(f"[1] _discover: {getattr(eng, 'v2ray_source_dir', '')!r}")
    assert getattr(eng, "v2ray_source_dir", ""), "v2ray 未发现"

    ok = eng.prepare()
    print(f"[2] prepare: {ok} work_dir={eng.work_dir}")
    assert ok, "prepare 失败"

    path = eng.write_config(NODE)
    built = json.load(open(path, encoding="utf-8"))
    vm = [o for o in built["outbounds"] if o.get("protocol") == "vmess"][0]
    v = vm["settings"]["vnext"][0]
    u = v["users"][0]
    print(f"[3] write_config: {path}")
    print(f"    vnext: {v['address']}:{v['port']} id={u['id'][:8]}... aid={u['alterId']}")
    print(f"    ws: path={vm['streamSettings']['wsSettings'].get('path')} host={vm['streamSettings']['wsSettings'].get('headers', {}).get('Host')}")
    socks = [i for i in built["inbounds"] if i.get("protocol") == "socks"]
    print(f"    socks inbound ports: {[i['port'] for i in socks]}")
    assert v["address"] == NODE["address"] and v["port"] == NODE["port"], "节点地址/端口未替换!"
    assert u["id"] == NODE["id"], "UUID 未替换!"
    assert any(i["port"] == eng.port for i in socks), "SOCKS 端口未替换!"

    ok = eng.start()
    print(f"[4] start: {ok} pid={eng.proc.pid if eng.proc else None}")
    assert ok, "v2ray 启动失败"

    ready = eng.wait_port(timeout=15)
    print(f"[5] wait_port: {ready}")
    assert ready, "端口未就绪"

    if eng.proc and eng.proc.poll() is not None:
        print(f"    !! v2ray 进程已退出 code={eng.proc.returncode}（配置被拒？）")

    direct = eng.check_direct_ip(timeout=8)
    print(f"[6] check_direct_ip: {direct!r}")

    proxy = eng.check_exit_ip(timeout=12)
    print(f"[7] check_exit_ip: {proxy!r}")
    print(f"[8] is_running: {eng.is_running()}")

    eng.stop()
    print(f"[9] stop done, is_running={eng.is_running()}")

    print("\n== 引擎自测通过 ==")

if __name__ == "__main__":
    main()
