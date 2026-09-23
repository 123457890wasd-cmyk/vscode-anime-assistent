"""
probe_js.py — 用 pywebview 的 evaluate_js 直接读 JS 状态，绕开 HTTP 回报通道

为什么要绕：之前用页面 POST 回报，结论在"透明/不透明"之间摇摆得反常，
怀疑回报通道本身不可靠。这里改成 Python 主动读 WebView 里的 JS 变量。

为什么用 url= 而不是 html=：真实场景（Airi 桌宠）是从本地 HTTP 服务加载的，
html= 走的是虚拟主机映射，是另一条路径，不能替真实情况作证。

判定：
  * 逐步推进到 done            -> 正常
  * 读到 webgl1:false          -> getContext 返回 null（可修）
  * 停在某步且 evaluate_js 无响应 -> getContext 阻塞了渲染器（致命）
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "probe_js_result.txt")
_lines = []


def out(m=""):
    _lines.append(m)
    try:
        print(m)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode("ascii"))
    try:
        with open(LOG, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines) + "\n")
    except OSError:
        pass


HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>js probe</title></head>
<body style="background:#123;color:#0f0;font:14px monospace">
<div id="out">booting</div>
<script>
window.__t = { steps: [], t0: Date.now(), errors: {} };
function step(name, extra) {
  window.__t.steps.push(name + (extra === undefined ? "" : (":" + extra)));
  var o = document.getElementById("out");
  if (o) o.textContent = window.__t.steps.join("\n");
}
step("script-start", location.protocol + "//" + location.host);
step("ua", navigator.userAgent.slice(36, 76));

try {
  var c2 = document.createElement("canvas");
  c2.width = c2.height = 64;
  var x2 = c2.getContext("2d");
  step("2d", !!x2);
} catch (e) { window.__t.errors.e2d = String(e); step("2d-threw"); }

try {
  var c1 = document.createElement("canvas");
  c1.width = c1.height = 64;
  step("before-webgl1");
  var x1 = c1.getContext("webgl");
  step("webgl1", !!x1);
  if (x1) window.__t.gl1 = x1.getParameter(x1.VERSION);
} catch (e) { window.__t.errors.e1 = String(e); step("webgl1-threw"); }

try {
  var c3 = document.createElement("canvas");
  c3.width = c3.height = 64;
  step("before-webgl2");
  var x3 = c3.getContext("webgl2");
  step("webgl2", !!x3);
  if (x3) window.__t.gl2 = x3.getParameter(x3.VERSION);
} catch (e) { window.__t.errors.e3 = String(e); step("webgl2-threw"); }

window.__t.done = true;
step("done");
</script>
</body></html>
"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        blob = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        try:
            self.wfile.write(blob)
        except Exception:
            pass


def patch_browser_args(extra):
    """
    pywebview 在 edgechromium.py 里直接给 props.AdditionalBrowserArguments 赋值，
    这个属性优先于 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS 环境变量 ——
    也就是说光设环境变量，Chromium 开关会被静默丢弃。必须拦它的 setter。
    """
    if not extra:
        return False
    try:
        from webview.platforms import edgechromium as ec
    except Exception as exc:
        out(f"  [warn] 无法导入 edgechromium: {exc!r}")
        return False

    orig_cls = ec.CoreWebView2CreationProperties

    class Patched(orig_cls):
        def __setattr__(self, name, value):
            if name == "AdditionalBrowserArguments" and isinstance(value, str):
                value = value + " " + extra
            super().__setattr__(name, value)

    ec.CoreWebView2CreationProperties = Patched
    out(f"  已 patch CoreWebView2CreationProperties，追加: {extra}")
    return True


def main():
    out("=" * 74)
    out("pywebview evaluate_js 直读探针 (url= 模式，与真实场景一致)")
    out("=" * 74)

    transparent = os.environ.get("PROBE_TRANSPARENT", "1") not in ("0", "false", "no")
    extra_args = os.environ.get("PROBE_WEBVIEW_ARGS", "").strip()
    per_call = float(os.environ.get("PROBE_EVAL_TIMEOUT", "6"))
    hard = float(os.environ.get("PROBE_HARD_TIMEOUT", "40"))
    port = int(os.environ.get("PROBE_PORT", "19950"))

    if extra_args:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = extra_args

    out(f"  transparent={transparent}  args={extra_args or '(none)'}")

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    out(f"  服务: {url}")

    import webview

    patch_browser_args(extra_args)

    def hard_stop():
        time.sleep(hard)
        out(f"  [FORCED EXIT] {hard:.0f}s 内没跑完")
        sys.stdout.flush()
        os._exit(0)

    threading.Thread(target=hard_stop, daemon=True).start()

    window = webview.create_window(
        "js probe", url=url,
        width=420, height=520, x=140, y=140,
        frameless=True, transparent=transparent, on_top=True,
        resizable=False, easy_drag=False,
    )

    state = {"loaded": False, "finished": False}

    def read_once(tag):
        box = {}

        def worker():
            try:
                box["v"] = window.evaluate_js(
                    "JSON.stringify({steps:window.__t?window.__t.steps:null,"
                    "done:!!(window.__t&&window.__t.done),"
                    "errors:window.__t?window.__t.errors:null,"
                    "gl1:(window.__t&&window.__t.gl1)||null,"
                    "gl2:(window.__t&&window.__t.gl2)||null})"
                )
            except Exception as exc:
                box["err"] = repr(exc)

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        th.join(per_call)
        if th.is_alive():
            out(f"    {tag}: <evaluate_js {per_call:.0f}s 无返回 —— 渲染器卡住>")
            return None
        if "err" in box:
            out(f"    {tag}: evaluate_js 抛错 {box['err']}")
            return None
        return box.get("v")

    def poll_loop():
        """不依赖 loaded 事件：直接开轮询。"""
        time.sleep(2.0)
        last = None
        for i in range(40):
            raw = read_once(f"poll#{i + 1}")
            if raw is None:
                break
            try:
                st = json.loads(raw)
            except Exception:
                out(f"    poll#{i + 1}: 非 JSON -> {raw!r}")
                break
            cur = json.dumps(st.get("steps"), ensure_ascii=False)
            if cur != last:
                out(f"    steps = {st.get('steps')}")
                if st.get("errors"):
                    out(f"    errors = {st.get('errors')}")
                last = cur
            if st.get("done"):
                if st.get("gl1"):
                    out(f"    gl1Version = {st.get('gl1')}")
                if st.get("gl2"):
                    out(f"    gl2Version = {st.get('gl2')}")
                out("  => 页面脚本完整执行完")
                state["finished"] = True
                break
            time.sleep(0.4)
        if not state["finished"]:
            out("  => 未跑完（最后成功读取的 steps 即为断点位置）")
        out()
        try:
            window.destroy()
        except Exception:
            pass

    def on_loaded():
        state["loaded"] = True
        out("  loaded 事件触发")

    window.events.loaded += on_loaded
    threading.Thread(target=poll_loop, daemon=True).start()

    out("  打开窗口 …")
    webview.start(debug=False)
    out(f"  窗口已关闭 (loaded={state['loaded']}, finished={state['finished']})")
    out()
    out("输出文件: " + LOG)


if __name__ == "__main__":
    main()
