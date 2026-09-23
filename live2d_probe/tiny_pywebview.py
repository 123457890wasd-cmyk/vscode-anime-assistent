"""
tiny_pywebview.py — 最小 pywebview 判定 + 窗口参数可调

结论已知：最朴素的窗口（html=、无 frameless/transparent/on_top）完全正常。
本脚本把窗口参数全部做成环境变量开关，用来二分到底哪一项让 WebView2 失效。

环境变量：
    TW_FRAMELESS=1  TW_TRANSPARENT=1  TW_ONTOP=1  TW_USE_URL=1
输出最后一行固定为：
    RESULT loaded=<0|1> shown=<0|1>
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "tiny_result.txt")

FRAMELESS = os.environ.get("TW_FRAMELESS", "0") == "1"
TRANSPARENT = os.environ.get("TW_TRANSPARENT", "0") == "1"
ONTOP = os.environ.get("TW_ONTOP", "0") == "1"
USE_URL = os.environ.get("TW_USE_URL", "0") == "1"
HARD = float(os.environ.get("TW_HARD", "25"))
TAG = os.environ.get("TW_TAG", "")

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


HTML = ('<!DOCTYPE html><html><body style="background:#2b2b2b;color:#0f0;'
        'font:18px monospace"><h1>pywebview OK</h1>'
        "<p>window params probe</p></body></html>")


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


def main():
    cfg = (f"frameless={int(FRAMELESS)} transparent={int(TRANSPARENT)} "
           f"on_top={int(ONTOP)} url={int(USE_URL)}")
    out("=" * 70)
    out("tiny pywebview  " + (f"[{TAG}] " if TAG else "") + cfg)
    out("=" * 70)

    import webview

    state = {"loaded": False, "shown": False}

    if USE_URL:
        port = int(os.environ.get("TW_PORT", "19990"))
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        target = {"url": f"http://127.0.0.1:{port}/"}
        out(f"  serving {target['url']}")
    else:
        target = {"html": HTML}

    window = webview.create_window(
        "tiny", width=360, height=220, x=200, y=200,
        frameless=FRAMELESS, transparent=TRANSPARENT, on_top=ONTOP,
        resizable=False, easy_drag=False, **target,
    )

    def on_loaded():
        state["loaded"] = True
        out("  loaded 事件触发")

    def on_shown():
        state["shown"] = True
        out("  shown 事件触发")

    window.events.loaded += on_loaded
    try:
        window.events.shown += on_shown
    except Exception:
        pass

    def hard():
        time.sleep(HARD)
        out(f"  [FORCED EXIT] {HARD:.0f}s")
        out()
        out(f"RESULT loaded={int(state['loaded'])} shown={int(state['shown'])}")
        sys.stdout.flush()
        os._exit(0)

    threading.Thread(target=hard, daemon=True).start()

    out("  webview.start()")
    webview.start(debug=False)
    out("  正常返回")
    out()
    out(f"RESULT loaded={int(state['loaded'])} shown={int(state['shown'])}")


if __name__ == "__main__":
    main()
