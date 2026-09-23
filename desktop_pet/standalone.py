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
import random
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from common import (get_screen_size, WindowAPI, find_character_image,
                    load_html, start_asset_server, disable_window_backdrop,
                    fix_window_background, win_bg_mode)
import live2d_assets

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

# ---------------------------------------------------------------------------
# 启动诊断 —— 同时打控制台和落盘
# ---------------------------------------------------------------------------
# 为什么必须落盘：VS Code 扩展是这样起桌宠的（src/extension.ts:launchStandalonePet）
#
#     spawn(python, [standalone.py], { cwd: petDir, detached: true,
#                                      stdio: 'ignore', windowsHide: true })
#
# **stdio:'ignore' 意味着这里 print 的任何东西都被丢掉。** 于是「Live2D 悄悄降级成
# 立绘」这类问题在用户侧零痕迹可查 —— 只能猜。所以关键诊断同时写一份到
# desktop_pet/.airi-pet.log（已 gitignore）。
#
# 每次启动会截断重写，只保留本次的日志，不会无限增长。
LOG_PATH = os.path.join(_script_dir, '.airi-pet.log')


def log(msg=''):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode('ascii', 'replace').decode('ascii'), flush=True)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except OSError:
        pass


def _init_log():
    try:
        with open(LOG_PATH, 'w', encoding='utf-8') as fh:
            fh.write('Airi 桌宠启动日志 —— 每次启动重写\n')
            fh.write(f'python  : {sys.executable}\n')
            fh.write(f'cwd     : {os.getcwd()}\n')
            fh.write(f'script  : {os.path.abspath(__file__)}\n')
            fh.write('-' * 60 + '\n')
    except OSError:
        pass


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
# 点击互动 —— 点角色身上不同位置，说不同的话
# ---------------------------------------------------------------------------
# 分区由 ui.html 的 zoneAt() 给出：canvas 上半是 head、中段是 body、底部是 desk。
# 每池多放几条，点着才不像复读机。
# 情绪词必须和 ui.html 的 EMOTION_EXPRESSION 对得上
# （idle / greeting / angry / happy / surprised），否则表情切不过去。
CLICK_LINES = {
    'head': (
        ('唔…别摸头啦，会长不高的。', 'surprised'),
        ('头发会乱的，你赔得起吗。', 'angry'),
        ('……再摸一下也行。就一下。', 'happy'),
        ('摸头杀对我没用哦，快去写代码。', 'greeting'),
    ),
    'body': (
        ('干嘛？我没偷懒，我在思考。', 'surprised'),
        ('你这个 bug 还没改完呢，别戳我。', 'angry'),
        ('戳我一次，我就少看你一行代码。', 'happy'),
        ('需要我帮你看报错吗？我不嫌你菜。', 'greeting'),
    ),
    'desk': (
        ('桌子是我的地盘，手拿开。', 'angry'),
        ('我在画图呢，别碰。', 'surprised'),
        ('……这张桌子陪我改过通宵的。', 'happy'),
        ('要不要一起喝点什么？你请。', 'greeting'),
    ),
}


def _pick_click_line(zone):
    """WindowAPI 的取词回调：返回 (台词, 情绪)。

    用量小而固定的本地语料，不走 response_generator —— 那个是给"诊断事件"
    生成回复的，点一下角色要的是**立刻**有反应，不该去碰网络/DEEPSEEK 那条路。
    """
    pool = CLICK_LINES.get(zone) or CLICK_LINES['body']
    return random.choice(pool)

# ---------------------------------------------------------------------------
# HTTP 服务器
# ---------------------------------------------------------------------------
class AiriHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def handle_error(self, request, client_address):
        """吞掉「客户端提前断开」的噪声。

        桌宠关窗时 SSE 长连接会被 reset，socketserver 默认会把
        ConnectionResetError/BrokenPipeError 打成整段 traceback，看起来像崩溃。
        真正意外的错误照旧抛出去。
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)

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
                kind = msg.get('type')
                if kind == 'desktopReady':
                    # 这行以前是 print —— 扩展用 stdio:'ignore' 起进程，print 全被丢掉，
                    # 于是"页面到底连上没有"在 .airi-pet.log 里毫无痕迹。必须走 log()。
                    log('[airi-standalone] Desktop pet connected')
                elif kind == 'silhouetteDiag':
                    # 前端上报的轮廓诊断：桥/尺寸/alpha 读数是否可信，全靠这条
                    p = msg.get('payload') or {}
                    log('[airi-sil] %s' % json.dumps(p, ensure_ascii=False))
            except Exception as exc:
                log(f'[airi-standalone] /event parse failed: {exc!r}')
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
            log(f'[airi-standalone] Airi is already running on port {PORT}, exit.')
            sys.exit(0)
        log(f'[airi-standalone] Port {PORT} is occupied by another program, exit.')
        sys.exit(1)
    char_img = find_character_image()
    if char_img:
        log(f'[airi-standalone] Using character image: {char_img}')

    # Live2D 素材可用就上 Live2D；不可用就安静地退回图片 / CSS 角色。
    # 任何一步失败都不该让桌宠起不来 —— 素材缺失、拿不到 Cubism Core 都属可恢复。
    l2d_ok, l2d_detail = live2d_assets.check_assets()
    for _ln in l2d_detail:
        log(f'[airi-live2d] {_ln}')
    log(f'[airi-live2d] live2d {"ENABLED" if l2d_ok else "disabled (image fallback)"}')

    # 「真透明」是两件独立的事，别再混在一起（详见 common.py 里的长注释）：
    #   1. 画面透出桌面 —— 靠关掉 DWM 的 Mica 背景材质。pywebview 在**系统深色
    #      模式**下会装 Mica，它由 DWM 绘制、不在窗口的 GDI 表面里，最终盖成
    #      一整块 #202020（浅色模式下不装，所以那时是 #F0F0F0 的 Form 底色）。
    #      这一条才是用户说的"并不是透明的"。见 disable_window_backdrop()。
    #   2. 点穿 —— 靠 SetWindowRgn 把窗口形状裁成"角色轮廓 + 气泡 + 名字"，
    #      形状之外不接受鼠标消息，点击落到下层窗口（比如 VS Code）。
    #      region 裁不到 DirectComposition 合成的 WebView2 内容，所以它
    #      **不改变画面**，只改变鼠标命中。
    sil = live2d_assets.silhouette_enabled()
    log(f'[airi-live2d] silhouette {"ON" if sil else "OFF"}')
    card = live2d_assets.card_mode()
    log(f'[airi-live2d] character card {card}')

    html = load_html(PORT, char_img, live2d=l2d_ok, silhouette=sil, card=card)
    page_url, asset_port = start_asset_server(html)
    if page_url is None:
        log('[airi-standalone] ERROR: cannot start asset server, exit.')
        sys.exit(1)
    log(f'[airi-standalone] UI served on {page_url}')
    try:
        import webview
    except ImportError:
        log('[airi-standalone] ERROR: pywebview not installed. Run: pip install pywebview')
        log(f'[airi-standalone] HTTP server still running on port {PORT}')
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
    api.set_click_handler(_pick_click_line)   # 点角色 -> 取一句台词
    api.set_region_enabled(sil)               # 异形窗口开关（前端也会自己判断一次）
    # 异形窗口必须留痕：这条链路跨 JS -> pywebview 桥 -> ctypes -> Win32 四层，
    # 任何一层断了以前都是静默的（用户只看到"窗口还是个方块"）。
    api.set_region_logger(lambda m: log(f'[airi-region] {m}'))
    window = webview.create_window(title='Airi', url=page_url, width=win_w, height=win_h, x=x, y=y, frameless=True, transparent=True, on_top=True, resizable=False, easy_drag=False, js_api=api)
    api.set_window(window)
    if _backend_available:
        from response_generator import pick_corpus
        push_message('chatMessage', pick_corpus('greeting'), 'greeting')
    else:
        push_message('chatMessage', '哼，我上线了。有错误我会骂你的，给我认真写！', 'greeting')
    def _after_window_ready():
        """窗口真正建出来之后才能做的两件事。

        1. 关掉 DWM 的 Mica 背景 —— 不关的话整窗是一块实心色（第三轮）
        2. 压掉/挖掉**宿主 Form 的浅灰底** —— 默认是 WinForms 的
           SystemColors.Control(#F0F0F0)，pywebview 走 transparent 分支时
           忘了给它赋值。它会从窗口 region 的 pad 环里露出来，就是用户报的
           "气泡/名牌周围的块状白"（第五轮）。见 common.fix_window_background()

        Form 是 webview.start() 内部才创建的，所以这里轮询等它出现（最多 20s）。
        这两样都只影响"窗口好不好看"，不是"能不能用" —— 超时只告警、不抛异常。
        """
        deadline = time.time() + 20
        detail = ''
        while time.time() < deadline:
            ok, detail = disable_window_backdrop(window)
            if ok:
                log(f'[airi-dwm] Mica backdrop disabled ({detail})')
                break
            time.sleep(0.25)
        else:
            log(f'[airi-dwm] WARNING: 没能关掉 Mica backdrop（{detail}）')

        bg_deadline = time.time() + 10
        while time.time() < bg_deadline:
            ok, detail = fix_window_background(window)
            if ok:
                log(f'[airi-winbg] Form 底色处理完毕 [{win_bg_mode()}] {detail}')
                return
            time.sleep(0.25)
        log(f'[airi-winbg] WARNING: 没能处理 Form 底色（{detail}）—— '
            '气泡/名牌周围可能还会有一圈浅色方块')

    log('[airi-standalone] Desktop pet starting...')
    try:
        webview.start(_after_window_ready, debug=False)
    except Exception:
        # 窗口起不来是最容易被 stdio:'ignore' 吃掉的一类失败 —— 必须留痕
        import traceback
        log('[airi-standalone] ERROR: webview.start() failed:')
        for _tln in traceback.format_exc().splitlines():
            log('    ' + _tln)
        raise

if __name__ == '__main__':
    _init_log()
    log('[airi-standalone] --- boot ---')
    main()
