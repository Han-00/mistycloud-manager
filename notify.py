"""飞书通知 — 换号成功/失败推送（对齐 AccountMasterPro switch_guard/feishu_notify）。

settings.json 可选字段（未配置则静默跳过，任何异常不影响换号主流程）：
  feishu_webhook                        群机器人 webhook（最简方式）
  feishu_app_id / feishu_app_secret /   开放平台应用（优先于 webhook）
  feishu_open_id
"""
import json

from config import config_path
from http_client import post_json


def _settings() -> dict:
    """直接读 settings.json（避免持有共享 Config 的生命周期问题）。"""
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def push(text: str) -> bool:
    """推送文本到飞书。返回 True=已发送（或已配置且发出），False=未配置/失败。"""
    st = _settings()
    try:
        app_id = str(st.get("feishu_app_id") or "")
        secret = str(st.get("feishu_app_secret") or "")
        open_id = str(st.get("feishu_open_id") or "")
        if app_id and secret and open_id:
            tok = post_json(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                data={"app_id": app_id, "app_secret": secret},
                json_body=True, retries=1, timeout=10)
            token = (tok or {}).get("tenant_access_token") if isinstance(tok, dict) else None
            if token:
                post_json(
                    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
                    headers={"Authorization": "Bearer " + token},
                    data={"receive_id": open_id, "msg_type": "text",
                          "content": json.dumps({"text": text})},
                    json_body=True, retries=1, timeout=10)
                return True
        hook = str(st.get("feishu_webhook") or "")
        if hook:
            post_json(hook,
                      data={"msg_type": "text", "content": {"text": text}},
                      json_body=True, retries=1, timeout=10)
            return True
    except Exception:
        return False
    return False


def notify_switch(email: str, success: bool, ip_before: str = "",
                  ip_after: str = "", error: str = "") -> bool:
    """换号结果通知。成功/失败共用一条链路。"""
    try:
        import time
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if success:
            lines = ["✅ 自动换号成功", f"账号: {email}"]
            if ip_before or ip_after:
                lines.append(f"出口: {ip_before or '?'} → {ip_after or '?'}")
        else:
            lines = ["❌ 自动换号失败", f"账号: {email}"]
            if error:
                lines.append(f"原因: {error}")
        lines.append(f"时间: {now}")
        return push("\n".join(lines))
    except Exception:
        return False
