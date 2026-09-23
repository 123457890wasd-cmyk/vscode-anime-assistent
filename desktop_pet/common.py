"""
common.py — Airi 桌宠共用工具

被 standalone.py / main.py 引用，提供：
  - get_screen_size():          主屏幕分辨率
  - WindowAPI:                  暴露给 ui.html (window.pywebview.api) 的窗口控制
  - find_character_image():     查找自定义立绘 assets/character.png
  - load_html(port, char_img):  读取 ui.html 并注入 {{PORT}} / {{CHARACTER_IMAGE}}
                                / {{LIVE2D_ENABLED}}
  - start_asset_server(html):   起本地静态服务器，返回可交给 pywebview 的 http URL

为什么不再写临时 HTML 文件（v0.3 起）
-------------------------------------
旧做法是把 ui.html 写到 %TEMP% 再用 file:// 打开。Live2D 一上来就撞在两堵墙上：

1. **`file://` 下 fetch/XHR 一律被 CORS 拦**，model3.json 及其引用的几十个文件
   一个都取不到 —— 不是"麻烦"，是**根本加载不了**。
2. WebView2 对 `file://` 下的 WebGL 贴图本来就容易出问题。

同源 HTTP 一次解决两件事。所以窗口改为指向 http://127.0.0.1:<asset_port>/，
静态资源由本模块的服务器提供（跟 SSE 服务器分开，SSE 那边完全不用动）。
"""

import ctypes
import os
import sys
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# 屏幕
# ---------------------------------------------------------------------------

def get_screen_size():
    """返回主屏幕分辨率 (width, height)。"""
    if sys.platform == 'win32':
        try:
            # 进程需要 DPI aware 才能拿到物理像素，否则 GetSystemMetrics
            # 返回缩放后的逻辑分辨率，导致窗口定位偏移
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            pass
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        size = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
        return size
    except Exception:
        return (1920, 1080)


# ---------------------------------------------------------------------------
# 窗口控制（pywebview js_api）
# ---------------------------------------------------------------------------

class WindowAPI:
    """ui.html 通过 window.pywebview.api 调用：拖拽移动 / 读取位置 / 隐藏。"""

    def __init__(self):
        self._window = None
        self._click_handler = None     # fn(zone) -> (text, emotion)，由宿主注入
        self._region_enabled = True    # 异形窗口（真透明）开关
        self._region_last = None       # 上次成功的矩形数，仅用于诊断
        self._region_log = None        # 宿主注入的日志函数（standalone 写进 .airi-pet.log）
        self._region_state = None      # 上次 (ok, why)，用于只在状态变化时打日志
        self._region_calls = 0

    def set_region_logger(self, fn):
        """宿主注入日志函数：fn(str)。

        为什么非要这个 —— 异形窗口以前是**静默降级**的：前端失败 3 次就永久
        放弃，宿主侧一句日志都没有。用户看到的就是"窗口还是个方块"，
        而日志里只到 "Desktop pet started"，完全无从判断卡在哪一环。
        这条链路跨 JS / 桥 / ctypes 三层，必须每一层都留痕。
        """
        self._region_log = fn

    def _log_region(self, msg):
        if self._region_log is None:
            return
        try:
            self._region_log(msg)
        except Exception:
            pass

    def set_window(self, window):
        self._window = window

    def get_position(self):
        if self._window is None:
            return [0, 0]
        return [self._window.x or 0, self._window.y or 0]

    def move_window(self, x, y):
        if self._window is not None:
            try:
                self._window.move(int(x), int(y))
            except Exception:
                pass

    def exit_app(self):
        """销毁窗口并退出进程（webview.start 返回后主线程结束，daemon 线程随之退出）"""
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 点击互动
    # ------------------------------------------------------------------

    def set_click_handler(self, fn):
        """宿主注入取词回调：fn(zone) -> (text, emotion)。

        台词语料放在宿主（standalone.py）而不是这里 —— common 只负责
        "前端点了角色 -> 问宿主要一句话 -> 回给前端"，不掺业务内容。
        """
        self._click_handler = fn

    def pet_click(self, zone):
        """ui.html 命中角色后调用，返回 {text, emotion}。

        前端只在**真的点到角色像素上**时才调这里（用 canvas alpha 判定），
        所以这里不必再管命中，只管取词。
        """
        try:
            if self._click_handler is not None:
                text, emotion = self._click_handler(str(zone or 'body'))
                return {'text': text or '', 'emotion': emotion or 'idle'}
        except Exception as exc:
            print(f'[airi-common] click handler failed: {exc!r}')
        return {'text': '', 'emotion': 'idle'}

    # ------------------------------------------------------------------
    # 异形窗口 —— 这是 Windows 上让桌宠"真透明"的唯一可行路线
    # ------------------------------------------------------------------

    def set_region_enabled(self, flag):
        self._region_enabled = bool(flag)

    def set_window_region(self, payload):
        """把窗口裁成 ui.html 上报的形状 —— 只影响**鼠标命中**，不负责画面透明。

        排查链的结论（实测数据见 live2d_probe/live_fix3.txt、live_fix4.txt）：
          1. 画面上的透明**不是这里做的**。pywebview 把 WebView2 那层设成
             DefaultBackgroundColor=Transparent，页面又是 background:transparent，
             所以角色之外本来就是透明的；用户看到的那块实心方块是
             **DWM 的 Mica 背景材质**，由 pywebview 的 update_title_bar_theme()
             在系统深色模式下装上。它不在 GDI 表面里，所以 region 裁不到它
             —— 这正是"region 设了却看不见效果"的原因。
             关掉 Mica 画面就是真透明（屏幕实测 0.00% → 90.49%），
             见 common.disable_window_backdrop()。
          2. region 在这里只负责**点穿**：窗口形状之外不接受鼠标消息，
             点角色以外的位置会落到下层窗口（比如 VS Code），
             不会让桌宠占着的那块地方变成点击黑洞。
             实测：客户区 (20,20)（形状之外）WindowFromPoint 命中的是别的窗口；
             (140,220)（形状之内）命中的是桌宠自己。

        payload = {'vw': 窗口 CSS 宽, 'vh': 窗口 CSS 高,
                   'rects': [[left, top, right, bottom], ...]}（页面 CSS 像素）
        """
        if not self._region_enabled:
            return {'ok': False, 'why': 'disabled'}
        if self._window is None:
            return {'ok': False, 'why': 'no window'}
        self._region_calls += 1
        try:
            p = payload or {}
            rects = p.get('rects') or []
            vw = float(p.get('vw') or 0) or 1.0
            vh = float(p.get('vh') or 0) or 1.0
            src = str(p.get('src') or '?')
            res = _apply_window_region(self._window, rects, vw, vh)
            res['src'] = src
            res['in_rects'] = len(rects)
            res['vw'] = vw
            res['vh'] = vh
            if res.get('ok'):
                self._region_last = res.get('rects')
            # 只在状态变化时打日志 + 失败时每 20 次补一条，保证日志不刷屏但绝不静默
            state = (bool(res.get('ok')), res.get('why'))
            if state != self._region_state or (
                    not res.get('ok') and self._region_calls % 20 == 0):
                self._region_state = state
                meta = res.get('meta') or {}
                self._log_region(
                    'region #%d src=%s in=%d out=%s ok=%s why=%s '
                    'hwnd=%s client=%s scale=%s vw=%s vh=%s'
                    % (self._region_calls, src, len(rects), res.get('rects'),
                       res.get('ok'), res.get('why'),
                       meta.get('hwnd'), meta.get('client'), meta.get('scale'),
                       int(vw), int(vh)))
            return res
        except Exception as exc:
            self._log_region('region #%d EXCEPTION %r' % (self._region_calls, exc))
            return {'ok': False, 'why': repr(exc)}


# --- 窗口 region 的底层实现 ------------------------------------------------

def _get_form(window):
    """取 pywebview 窗口背后的 WinForms BrowserForm 实例。

    pywebview 没有公开 HWND，只能从它的 winforms 后端翻 BrowserForm 实例
    （winforms.py 里 `BrowserView.instances[window.uid] = browser`）。
    取不到返回 None，调用方一律静默降级 —— 这些都是锦上添花，
    绝不该因为它们让桌宠起不来。
    """
    try:
        from webview.platforms import winforms as _wf
        instances = getattr(_wf.BrowserView, 'instances', None)
        if not instances:
            return None
        uid = getattr(window, 'uid', None)
        form = instances.get(uid) if uid is not None else None
        if form is None:
            form = list(instances.values())[-1]
        return form
    except Exception:
        return None


def _get_hwnd(window):
    """取 pywebview 窗口背后的 Win32 HWND（取不到返回 0）。"""
    form = _get_form(window)
    if form is None:
        return 0
    try:
        return int(form.Handle.ToInt64())
    except Exception:
        return 0


# --- DWM 背景材质（Mica）----------------------------------------------------
#
# 这是「窗口根本不透明」的**真正根因**，别再往别处找。
#
# pywebview 的 BrowserForm.update_title_bar_theme()（winforms.py:333）在
# **系统深色模式**下会执行：
#     DwmSetWindowAttribute(hwnd, 38, 2)   # 38=DWMWA_SYSTEMBACKDROP_TYPE
#                                          # 2=DWMSBT_MAINWINDOW，也就是 Mica
# 浅色模式下它设的是 1(DWMSBT_NONE)，不装。
#
# Mica 由 DWM 自己绘制，**不在窗口的 GDI 重定向表面里**。这一条一举解释了
# 之前全部三个"见了鬼"的现象（实测见 live2d_probe/live_fix3.txt）：
#   * SetWindowRgn 返回成功、GetWindowRgn 也能读回 138x220，屏幕上一像素不裁；
#   * SetLayeredWindowAttributes(LWA_COLORKEY, #202020) 返回成功，挖不掉底色；
#   * 只有整窗 LWA_ALPHA 有效 —— 因为它作用在最终合成结果上。
# 屏幕实测（live2d_probe/live_fix4.txt，可反向复现）：
#     backdrop=2  → 窗口区域与桌面一致的像素 0.00%（整块不透明）
#     改成 NONE   → 立刻 90.49%（角色周围真透出桌面）
#     改回 2      → 又回到 0.00%
#
# 这还顺带解释了用户两张截图为什么颜色不同：那块底色跟着系统主题在
# #F0F0F0（浅）/ #202020（深）之间变，而 SystemColors.Control 恰好也是这两个值。

DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE = 1


def _set_backdrop_none(hwnd):
    """把窗口的 DWM 背景材质设成 None（关掉 Mica）。成功返回 True。"""
    if not hwnd:
        return False
    try:
        v = ctypes.c_int(DWMSBT_NONE)
        rc = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(int(hwnd)),
            ctypes.c_uint(DWMWA_SYSTEMBACKDROP_TYPE),
            ctypes.byref(v), ctypes.c_uint(ctypes.sizeof(v)))
        return rc == 0
    except Exception:
        return False


def get_backdrop_type(hwnd):
    """读回 DWMWA_SYSTEMBACKDROP_TYPE，仅用于诊断；读不到返回 -1。"""
    try:
        v = ctypes.c_int(-1)
        rc = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(int(hwnd)),
            ctypes.c_uint(DWMWA_SYSTEMBACKDROP_TYPE),
            ctypes.byref(v), ctypes.c_uint(ctypes.sizeof(v)))
        return v.value if rc == 0 else -1
    except Exception:
        return -1


def disable_window_backdrop(window):
    """关掉 DWM 的 Mica 背景，并防止它被重新装上。返回 (ok, detail)。

    为什么还要"防止重新装上"：切换系统主题会触发
    SystemEvents.UserPreferenceChanged -> update_title_bar_theme()，
    深色模式下 Mica 会被**重新装回去**，窗口立刻又变回一块实心方块。
    所以这里把该实例方法包一层，之后每次调用完都再按一次。
    """
    if sys.platform != 'win32':
        return False, 'not win32'
    form = _get_form(window)
    if form is None:
        return False, 'no BrowserForm instance yet'
    hwnd = _get_hwnd(window)
    if not hwnd:
        return False, 'no hwnd'

    before = get_backdrop_type(hwnd)
    ok = _set_backdrop_none(hwnd)
    after = get_backdrop_type(hwnd)

    guarded = bool(getattr(form, '_airi_backdrop_guarded', False))
    if not guarded:
        try:
            original = form.update_title_bar_theme

            def _guarded():
                try:
                    original()
                finally:
                    _set_backdrop_none(_get_hwnd(window))

            form.update_title_bar_theme = _guarded
            form._airi_backdrop_guarded = True
            guarded = True
        except Exception:
            pass

    detail = ('hwnd=%s backdrop=%s->%s set=%s guarded=%s'
              % (hex(hwnd), before, after, ok, guarded))
    return (ok and after == DWMSBT_NONE), detail


class _RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


# --- 宿主 Form 的底色（第五轮找到的"块状白"真正来源）------------------------
#
# pywebview 的 winforms.py:286-292 在 transparent=True 时只做了两件事：
#     self.SetStyle(WinForms.ControlStyles.SupportsTransparentBackColor, True)
#     self.browser.DefaultBackgroundColor = Color.Transparent
# **它没有给 Form.BackColor 赋值** —— 走透明分支时那句
# `self.BackColor = ColorTranslator.FromHtml(window.background_color)` 在 else 里，
# 于是 Form 保持 WinForms 的默认底色 SystemColors.Control（浅色主题 = #F0F0F0）。
#
# WebView2 那层确实是透明的，但"透明"露出来的是**这张浅灰底**。
# 平时看不到，是因为窗口被 region 裁成了 DOM 元素的形状 —— region 之外整个
# 窗口被剪掉，显出来的是桌面。而 region 的矩形是 DOM 盒**加了 pad** 的
# （见 ui.html collectUiRects），那一圈 pad 环里页面什么都没画 ⇒ 浅灰底露出来。
#
# 用户截图里的三块"白"因此全部对得上（第五轮实测，见 live2d_probe/README.md）：
#     气泡  : DOM 盒 212x36.6 + pad5 -> 222x46.6   截图实测 222x46   ✓
#     Airi  : DOM 盒  33x15.6 + pad3 ->  39x21.6   截图实测  40x20   ✓
#     online: DOM 盒  37x14   + pad3 ->  43x20     截图实测  44x20   ✓
#
# 修法：把 Form 底色设成**一个画面上不会出现的颜色**，同时拿这个颜色当
# TransparencyKey —— WinForms 会据此走 LWA_COLORKEY，把"页面没画"的像素
# 真正挖成洞（桌面透出来，而且那些像素自动点穿）。
# 第三轮试过 LWA_COLORKEY 说"挖不掉底色"，原因是**键色猜错了**：
# 当时用 #202020（深色）去挖 #F0F0F0（浅灰）的底，自然一像素都不动。
#
# AIRI_WIN_BG:
#   key   默认。底色=键色=#010203，真挖洞
#   dark  只把底色压成近黑，不打洞（挖不动时的退路，至少不刺眼）
#   off   完全不动（A/B 用）
FORM_BG_KEY = (1, 2, 3)
WIN_BG_MODES = ('key', 'dark', 'off')


def win_bg_mode():
    v = os.environ.get('AIRI_WIN_BG', '').strip().lower()
    return v if v in WIN_BG_MODES else 'key'


def fix_window_background(window):
    """把宿主 Form 的浅灰底压掉/挖掉，返回 (ok, detail)。

    不动的后果：窗口 region 里凡是页面没画的像素都是 #F0F0F0 的浅灰，
    表现为气泡/名牌/名字周围一圈"块状白"。详见上面那段长注释。
    """
    mode = win_bg_mode()
    if mode == 'off':
        return (False, 'AIRI_WIN_BG=off（不动底色）')
    form = _get_form(window)
    if form is None:
        return (False, 'no form')
    try:
        before = str(form.BackColor)
    except Exception:
        before = '?'
    try:
        from System.Drawing import Color
        rgb = FORM_BG_KEY if mode == 'key' else (0, 0, 0)
        col = Color.FromArgb(rgb[0], rgb[1], rgb[2])
        form.BackColor = col
        hole = 'no'
        if mode == 'key':
            # TransparencyKey 会顺手装上 WS_EX_LAYERED + LWA_COLORKEY；
            # 之后页面里任何**恰好等于键色**的像素也会变成洞 —— 这就是把
            # 键色选成 #010203 而不是黑/白的原因：画面上不可能出现这个色。
            form.TransparencyKey = col
            hole = 'yes'
        try:
            now = str(form.BackColor)
        except Exception:
            now = '?'
        return (True, 'backdrop=%s -> %s hole=%s key=#%02X%02X%02X'
                % (before, now, hole, rgb[0], rgb[1], rgb[2]))
    except Exception as e:                     # 锦上添花，绝不因此起不来
        return (False, 'set failed: %s' % e)


def _apply_window_region(window, rects, vw, vh):
    """按前端上报的矩形重建窗口 region。

    两组换算缺一不可：
      * 页面 CSS 像素 -> 窗口客户区**物理**像素（1.25 / 1.5 缩放很常见，
        用 GetClientRect 的实际尺寸除以前端上报的视口尺寸，比自己猜 DPI 稳）
      * region 坐标系原点就是客户区左上角；frameless 窗口没有非客户区，不用再减
    """
    meta = {}
    if sys.platform != 'win32':
        return {'ok': False, 'why': 'not win32', 'meta': meta}
    if not rects:
        # 空列表 = 整个窗口都不可见。宁可不裁，也绝不做出一个
        # "看不见又关不掉"的窗口。
        return {'ok': False, 'why': 'empty rects (refused)', 'meta': meta}

    hwnd = _get_hwnd(window)
    meta['hwnd'] = hex(hwnd) if hwnd else '0'
    if not hwnd:
        return {'ok': False, 'why': 'no hwnd', 'meta': meta}

    # 保险：region 只管鼠标命中，画面透明靠的是 Mica 关着。
    # 万一主题切换把它装回来了，这里顺手再按一次（单次 syscall，很便宜）。
    _set_backdrop_none(hwnd)

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.ExtCreateRegion.restype = ctypes.c_void_p
    gdi32.ExtCreateRegion.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.c_void_p]
    user32.SetWindowRgn.restype = ctypes.c_int
    user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]

    rc = _RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return {'ok': False, 'why': 'GetClientRect failed', 'meta': meta}
    cw, ch = rc.right - rc.left, rc.bottom - rc.top
    meta['client'] = '%dx%d' % (cw, ch)
    if cw <= 1 or ch <= 1:
        return {'ok': False, 'why': 'client rect empty', 'meta': meta}

    sx, sy = cw / vw, ch / vh
    meta['scale'] = '%.4f,%.4f' % (sx, sy)

    packed = []
    minx, miny, maxx, maxy = cw, ch, 0, 0
    for r in rects:
        try:
            l = int(round(float(r[0]) * sx)); t = int(round(float(r[1]) * sy))
            rr = int(round(float(r[2]) * sx)); b = int(round(float(r[3]) * sy))
        except (TypeError, ValueError, IndexError):
            continue
        l = max(0, min(cw, l)); rr = max(0, min(cw, rr))
        t = max(0, min(ch, t)); b = max(0, min(ch, b))
        if rr <= l or b <= t:
            continue
        packed.append((l, t, rr, b))
        minx = min(minx, l); miny = min(miny, t)
        maxx = max(maxx, rr); maxy = max(maxy, b)

    meta['kept'] = len(packed)
    if not packed:
        return {'ok': False, 'why': 'all rects clipped away', 'meta': meta}

    import struct
    n = len(packed)
    # RGNDATAHEADER 恰好 32 字节：dwSize/iType/nCount/nRgnSize + rcBound(16)
    blob = (struct.pack('<IIIIiiii', 32, 1, n, n * 16, minx, miny, maxx, maxy)
            + b''.join(struct.pack('<iiii', *r) for r in packed))
    buf = ctypes.create_string_buffer(blob, len(blob))
    hrgn = gdi32.ExtCreateRegion(None, len(blob), buf)
    if not hrgn:
        return {'ok': False, 'why': 'ExtCreateRegion failed', 'meta': meta}
    # SetWindowRgn 成功后 region 归系统所有，不能再 DeleteObject
    if not user32.SetWindowRgn(hwnd, hrgn, True):
        return {'ok': False, 'why': 'SetWindowRgn failed', 'meta': meta}
    meta['bbox'] = '%d,%d,%d,%d' % (minx, miny, maxx, maxy)
    return {'ok': True, 'rects': n, 'meta': meta}


# ---------------------------------------------------------------------------
# 立绘与 HTML
# ---------------------------------------------------------------------------

def find_character_image():
    """查找自定义立绘，找不到返回 None（ui.html 会退回 CSS 角色）。

    优先级：AIRI_CHARACTER_IMAGE 环境变量 > assets/character.* > 根目录 character.png
    """
    env_path = os.environ.get('AIRI_CHARACTER_IMAGE', '').strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)
        print(f'[airi-common] WARNING: AIRI_CHARACTER_IMAGE not found: {env_path}')
    for name in ('character.png', 'character.gif', 'character.webp'):
        p = _SCRIPT_DIR / 'assets' / name
        if p.is_file():
            return str(p)
    p = _SCRIPT_DIR / 'character.png'
    return str(p) if p.is_file() else None


def load_html(port, char_img=None, live2d=False, silhouette=False, card='square'):
    """读取 ui.html，注入端口、立绘 file:// URI 和各个开关，返回最终 HTML 字符串。

    live2d 为 True 时才让页面去加载引擎；素材缺失时保持 False，
    页面就还走原来的图片 / CSS 角色，不会在控制台刷一堆 404。

    silhouette 为 True 时页面会定期把「角色轮廓 + 气泡 + 名字」的形状上报给
    WindowAPI.set_window_region()，由 Python 侧 SetWindowRgn 把窗口裁成异形。
    ⚠️ 它**只管鼠标点穿**，不管画面透明 —— 画面透明是关掉 DWM 的 Mica 背景材质
    （见 disable_window_backdrop()）。

    card 是角色底板模式（off/tight/square/frame/all），见 live2d_assets.card_mode()。
    默认与 card_mode() 保持一致 —— 两处默认值不一样的话，将来谁漏传一次 card，
    就会得到和界面其它地方不同的观感，很难查。
    """
    html = (_SCRIPT_DIR / 'ui.html').read_text(encoding='utf-8')
    img_uri = Path(char_img).as_uri() if char_img else ''
    html = html.replace('{{PORT}}', str(int(port)))
    html = html.replace('{{CHARACTER_IMAGE}}', img_uri)
    html = html.replace('{{LIVE2D_ENABLED}}', 'true' if live2d else 'false')
    html = html.replace('{{SILHOUETTE}}', 'true' if silhouette else 'false')
    html = html.replace('{{CHAR_CARD}}', str(card or 'off'))
    return html


# ---------------------------------------------------------------------------
# 本地静态资源服务器（Live2D 素材 + 页面本体）
# ---------------------------------------------------------------------------

_MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.txt': 'text/plain; charset=utf-8',
    '.md': 'text/plain; charset=utf-8',
    # Cubism 原生网格，无标准 MIME
    '.moc3': 'application/octet-stream',
}


def _safe_join(base: Path, url_path: str):
    """把 URL 路径安全映射到 base 之下；越界（含 `..`、绝对路径）返回 None。"""
    rel = urllib.parse.unquote(url_path)
    rel = rel.replace('\\', '/').lstrip('/')
    if not rel:
        return None
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target


def _make_handler(html_text: str):
    """闭包出一个绑定了当前 HTML 的处理器类。"""
    from live2d_assets import VENDOR_DIR, MODEL_ROOT

    page = html_text.encode('utf-8')

    class _AssetHandler(SimpleHTTPRequestHandler):
        server_version = 'AiriAsset/1.0'
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            pass          # 桌宠不该往控制台刷请求日志

        def handle_error(self, request, client_address):
            """吞掉「客户端提前断开」这类噪声。

            WebView2 关窗/刷新时，正在传输的请求会被 reset，socketserver 的默认
            handle_error 会把 ConnectionResetError / BrokenPipeError 打成一整段
            traceback —— 看起来像崩溃，其实是正常收尾。真正意外的错误照旧抛出去。
            """
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                                BrokenPipeError, TimeoutError)):
                return
            super().handle_error(request, client_address)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path

            if path in ('/', '/index.html', '/ui.html'):
                return self._blob(page, 'text/html; charset=utf-8')

            if path == '/favicon.ico':
                return self._plain(204, b'')

            # 引擎 js 与 core
            if path.startswith('/vendor/'):
                target = _safe_join(VENDOR_DIR, path[len('/vendor/'):])
                if target is None:
                    return self._plain(403, b'forbidden path')
                if not target.is_file():
                    return self._plain(404, b'not found: ' + path.encode('utf-8'))
                return self._file(target)

            # Live2D 模型闭包
            if path.startswith('/pet-assets/'):
                target = _safe_join(MODEL_ROOT, path[len('/pet-assets/'):])
                if target is None:
                    return self._plain(403, b'forbidden path')
                if not target.is_file():
                    return self._plain(404, b'not found: ' + path.encode('utf-8'))
                return self._file(target)

            return self._plain(404, b'not found: ' + path.encode('utf-8'))

        def _file(self, target: Path):
            try:
                blob = target.read_bytes()
            except OSError:
                return self._plain(404, b'unreadable')
            ctype = _MIME.get(target.suffix.lower(), 'application/octet-stream')
            return self._blob(blob, ctype)

        def _blob(self, blob: bytes, ctype: str):
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(blob)))
            # 桌宠每次启动都重读本地文件，别让 WebView2 缓存住旧模型
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(blob)
            except Exception:
                pass

        def _plain(self, code: int, blob: bytes):
            try:
                self.send_response(code)
                # HTTP/1.1 keep-alive 必须有准确的 Content-Length；
                # 204/304 按定义没有 body，不能带
                if code not in (204, 304):
                    self.send_header('Content-Length', str(len(blob)))
                self.end_headers()
                if blob:
                    self.wfile.write(blob)
            except Exception:
                pass

    return _AssetHandler


def start_asset_server(html_text: str, port: int = 0):
    """起一个本地静态服务器，返回 (base_url, actual_port)。失败返回 (None, 0)。

    port=0 表示让系统分配空闲端口；AIRI_ASSET_PORT 可固定端口，便于调试。
    """
    if not port:
        try:
            port = int(os.environ.get('AIRI_ASSET_PORT', '') or 0)
        except ValueError:
            port = 0

    try:
        srv = ThreadingHTTPServer(('127.0.0.1', port), _make_handler(html_text))
    except OSError as exc:
        print(f'[airi-common] asset server failed to bind: {exc!r}')
        return None, 0

    srv.daemon_threads = True
    actual = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{actual}/', actual
