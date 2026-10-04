"""隐藏的原生 WebView2 冒烟验证，只用演示桥和临时浏览器目录。"""
import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from traffic_analysis import build_report
from ui_web import _webui_app


class DemoApi:
    def refresh(self):
        return None

    def get_settings(self):
        return {}

    def get_routing_rules(self):
        return {"rules": [], "builtin_domains": [], "legacy_direct_domains": [], "applying": False}

    def get_traffic_report(self, start, end):
        today = date.today()
        daily = {(today-timedelta(days=i)).isoformat():
                 {"bytes": (i % 7+1)*1048576, "samples": 2, "intervals": 1}
                 for i in range(90)}
        return build_report({"daily": daily}, start, end)


def main():
    results = []
    # WebView2 子进程退出稍晚于窗口，清理不应掩盖验证结果。
    with tempfile.TemporaryDirectory(prefix="account-master-ui-check-", ignore_cleanup_errors=True) as storage:
        window = webview.create_window("界面加载验证", _webui_app, width=920,
                                       height=640, hidden=True, js_api=DemoApi())

        def check():
            try:
                if not window.events.loaded.wait(20):
                    raise AssertionError("原生页面未加载")
                for _ in range(50):
                    ready = window.evaluate_js("!!(window.App && window.Traffic && window.pywebview && window.pywebview.api.get_traffic_report)")
                    if ready:
                        break
                    time.sleep(.1)
                assert ready, "前端或原生桥未就绪"
                window.evaluate_js("window.__uiErrors=[]; window.addEventListener('error', e=>__uiErrors.push(e.message)); window.addEventListener('unhandledrejection', e=>__uiErrors.push(String(e.reason)));")
                for name in ("dash", "traffic", "pool", "routing", "diagnostics", "logs", "settings"):
                    window.evaluate_js(f"document.querySelector('[data-page={name}]').click()")
                    time.sleep(.15)
                    shown = window.evaluate_js("[...document.querySelectorAll('.page.show')].map(p=>p.id)")
                    assert shown == ["page-"+name], (name, shown)
                window.evaluate_js("document.querySelector('[data-page=traffic]').click()")
                for _ in range(50):
                    if window.evaluate_js("!Traffic.loading && !!Traffic.report"):
                        break
                    time.sleep(.1)
                result = window.evaluate_js("({title:document.title, version:document.querySelector('.brand .ver').textContent, nav:[...document.querySelectorAll('.nav-item')].map(n=>n.textContent.trim()), rows:document.querySelectorAll('#trafficRows tr').length, curves:document.querySelectorAll('#trafficChart .chart-line').length, dots:document.querySelectorAll('#trafficChart circle').length, styled:getComputedStyle(document.body).backgroundColor!=='rgba(0, 0, 0, 0)', errors:__uiErrors})")
                assert result["title"] == "账号大师 Pro 3.0", result
                assert result["version"] == "Pro v3.0", result
                assert result["rows"] == 30 and result["curves"] > 0 and result["dots"] == 0, result
                assert result["styled"] and not result["errors"], result
                results.append({"ok": True, "renderer": "edgechromium", **result})
            except Exception as error:
                results.append({"ok": False, "error": str(error)})
            finally:
                window.destroy()

        webview.start(check, gui="edgechromium", private_mode=False, storage_path=storage)
    print(json.dumps(results, ensure_ascii=False))
    return 0 if results and results[0]["ok"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
