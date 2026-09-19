"""
standalone.py — Airi 桌面宠物独立启动器

一键启动：python standalone.py
不依赖 VS Code 扩展，内置 SSE 服务器 + pywebview 窗口。

可选：VS Code 扩展可向 http://127.0.0.1:<port>/push POST 消息来推送诊断事件
（端口默认 19876，可用环境变量 AIRI_STANDALONE_PORT 覆盖）。
"""

import sys
import os
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from common import get_screen_size, WindowAPI, find_character_image, load_html, create_temp_html

# 导入 Python 后端（回复生成器）
_script_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.join(os.path.dirname(_script_dir), 'python_backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
try:
    from response_generator import generate_response
    _backend_available = True
except Exception as e:
    _backend_available = False
    print(f'[airi-standalone] WARNING: Python backend not available: {e!r}')

# 多线程 HTTP 服务器
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

# ---------------------------------------------------------------------------
# SSE 消息队列（线程安全，有上限防内存泄漏）
# 每条消息带递增序号：队列裁剪后客户端仍按序号取新消息，不会错位
# ---------------------------------------------------------------------------
MAX_QUEUE_SIZE = 200
SSE_REPLAY_COUNT = 20   # 新连接重播最近的消息数（含启动问候）
sse_queue = []          # [(seq, json_str)]
_seq = 0
queue_lock = threading.Lock()

def push_message(msg_type: str, text: str, emotion: str = "idle"):
    """向 SSE 客户端推送消息。超过上限时丢弃最旧的消息。"""
    global _seq
    with queue_lock:
        _seq += 1
        sse_queue.append((_seq, json.dumps(
            {"type": msg_type, "payload": {"text": text, "emotion": emotion}},
            ensure_ascii=False
        )))
        if len(sse_queue) > MAX_QUEUE_SIZE:
            del sse_queue[:len(sse_queue) - MAX_QUEUE_SIZE]

# ---------------------------------------------------------------------------
# HTTP 服务器
# ---------------------------------------------------------------------------
class AiriHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b':ok\n\n')
            last_seq = 0
            try:
                # 新连接先重播最近几条（让刚打开的窗口能看到问候语）
                with queue_lock:
                    replay = sse_queue[-SSE_REPLAY_COUNT:]
                    if replay:
                        last_seq = replay[-1][0]
                for _seq_no, d in replay:
                    self.wfile.write(f'data: {d}\n\n'.encode('utf-8'))
                self.wfile.flush()
                while True:
                    time.sleep(0.3)
                    with queue_lock:
                        new_msgs = [(s, d) for (s, d) in sse_queue if s > last_seq]
                        if new_msgs:
                            last_seq = new_msgs[-1][0]
                    for _seq_no, d in new_msgs:
                        try:
                            self.wfile.write(f'data: {d}\n\n'.encode('utf-8'))
                            self.wfile.flush()
                        except Exception:
                            return
            except Exception:
                pass
        elif self.path == '/ping':
            self._json_response({'status': 'ok', 'server': 'airi-standalone'})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path in ('/push', '/diagnostics'):
            try:
                length = int(self.headers.get('Content-Length', 0))
            except (TypeError, ValueError):
                self.send_response(400)
                self.end_headers()
                return
            body = self.rfile.read(length).decode('utf-8')
            try:
                msg = json.loads(body)
            except Exception:
                push_message('chatMessage', body, 'idle')
                self._json_response({'status': 'pushed'})
                return
            if 'payload' in msg and 'text' in msg.get('payload', {}):
                t = msg.get('type', 'chatMessage')
                tx = msg['payload']['text']
                em = msg['payload'].get('emotion', 'idle')
                push_message(t, tx, em)
            elif msg.get('type') == 'diagnostics' and 'payload' in msg:
                p = msg['payload']
                ctx = {
                    'trigger': p.get('trigger', 'diagnostics'),
                    'error_count': p.get('count', 0),
                    'language': p.get('language', 'unknown'),
                    'sample_errors': p.get('items', [])[:5],
                }
                if _backend_available:
                    r = generate_response(ctx)
                    if r:
                        push_message(r.get('type', 'chatMessage'), r.get('payload', {}).get('text', ''), r.get('payload', {}).get('emotion', 'idle'))
                else:
                    # 后端不可用时的内置兜底台词
                    if ctx['error_count'] > 0:
                        push_message('errorAlert', f"喂！{ctx['error_count']} 个错误！给我认真点检查！", 'angry')
                    else:
                        push_message('chatMessage', '哼，全部修好了…算你厉害。', 'happy')
            elif 'trigger' in msg or 'error_count' in msg:
                if _backend_available:
                    r = generate_response(msg)
                    if r:
                        push_message(r.get('type'), r.get('payload', {}).get('text', ''), r.get('payload', {}).get('emotion', 'idle'))
            else:
                push_message('chatMessage', msg.get('message', body), 'idle')
            self._json_response({'status': 'pushed'})
        elif self.path == '/event':
            try:
                length = int(self.headers.get('Content-Length', 0))
            except (TypeError, ValueError):
                self.send_response(400)
                self.end_headers()
                return
            body = self.rfile.read(length).decode('utf-8')
            try:
                msg = json.loads(body)
                if msg.get('type') == 'desktopReady':
                    print('[airi-standalone] Desktop pet connected')
            except Exception:
                pass
            self._json_response({'status': 'ok'})
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _json_response(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

def start_server(port: int, ready: threading.Event):
    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), AiriHandler)
    except Exception as e:
        print(f'[airi-standalone] HTTP server ERROR: {e}', flush=True)
        return
    print(f'[airi-standalone] HTTP server on http://127.0.0.1:{port}', flush=True)
    ready.set()
    server.serve_forever()

def _ping_ok(port: int) -> bool:
    """探测端口上是否已有 Airi 服务器在运行（/ping 返回 200）"""
    try:
        import urllib.request
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/ping', timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False

def main():
    try:
        PORT = int(os.environ.get('AIRI_STANDALONE_PORT', '') or 19876)
    except ValueError:
        PORT = 19876
    server_ready = threading.Event()
    server_thread = threading.Thread(target=start_server, args=(PORT, server_ready), daemon=True)
    server_thread.start()
    if not server_ready.wait(timeout=5.0):
        # 绑定失败：区分「已有 Airi 实例」和「端口被无关程序占用」，
        # 两种情况都不能继续开窗（否则会弹出一个连不上自己服务器的僵尸窗口）
        if _ping_ok(PORT):
            print(f'[airi-standalone] Airi is already running on port {PORT}, exit.', flush=True)
            sys.exit(0)
        print(f'[airi-standalone] Port {PORT} is occupied by another program, exit.', flush=True)
        sys.exit(1)
    char_img = find_character_image()
    if char_img:
        print(f'[airi-standalone] Using character image: {char_img}')
    html = load_html(PORT, char_img)
    html_url = create_temp_html(html, 'airi_standalone_ui.html')
    try:
        import webview
    except ImportError:
        print("[airi-standalone] ERROR: pywebview not installed. Run: pip install pywebview")
        print("[airi-standalone] HTTP server still running on port", PORT)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        sys.exit(1)
    screen_w, screen_h = get_screen_size()
    win_w, win_h = 300, 480
    x = screen_w - win_w - 40
    y = screen_h - win_h - 120
    api = WindowAPI()
    window = webview.create_window(title='Airi', url=html_url, width=win_w, height=win_h, x=x, y=y, frameless=True, transparent=True, on_top=True, resizable=False, easy_drag=False, js_api=api)
    api.set_window(window)
    if _backend_available:
        from response_generator import pick_corpus
        push_message('chatMessage', pick_corpus('greeting'), 'greeting')
    else:
        push_message('chatMessage', '哼，我上线了。有错误我会骂你的，给我认真写！', 'greeting')
    print('[airi-standalone] Desktop pet starting...', flush=True)
    webview.start(debug=False)

if __name__ == '__main__':
    main()
