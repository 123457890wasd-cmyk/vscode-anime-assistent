# -*- coding: utf-8 -*-
"""实测 pywebview 的"白框"能不能去掉 —— 用窗口自身的渲染结果说话。

已确证的根因（截图像素 + 运行时属性双证据）：
    用户截图窗口内部 = #F0F0F0 = SystemColors.Control
    运行时读 Form.BackColor = Color [Control]
    => WebView2 那一层**已经透明**了，挡住的是宿主 WinForms Form 的默认背景。
    pywebview 6.2.1 winforms.py:286-292 的 transparent 分支只调了
    SetStyle(SupportsTransparentBackColor, True)，没设 BackColor（那句在 else 分支）。

采坑记录（很重要，避免下次再错）：
    × GetPixel(GetDC(NULL))      —— 对 layered / 硬件加速窗口读数不可信
    × BitBlt(GetDC(NULL), CAPTUREBLT) —— 抓不到 WebView2 的内容
      （WebView2 走 DirectComposition，不在 screen DC 里；窗口区域永远全黑，
        所以"窗口变黑"这种结论是假的）
    √ PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT) —— 能拿到 DirectComposition 内容，
      用来判断「窗口升级为 layered 之后 WebView2 还渲不渲染」。

用法：
    python probe_transparency.py baseline|transkey|colorkey|ellipse
产物：
    tp_<variant>.png    BitBlt 屏幕抓取（窗口区域不可信，仅看边界/底色）
    tpw_<variant>.png   PrintWindow 窗口自身（看 WebView2 到底画了什么）
"""
import sys
import os
import time
import zlib
import struct
import threading
import traceback

VAR = (sys.argv[1] if len(sys.argv) > 1 else 'baseline').lower()
HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, 'transparency_result.txt')
SHOT_SCREEN = os.path.join(HERE, 'tp_%s.png' % VAR)
SHOT_WIN = os.path.join(HERE, 'tpw_%s.png' % VAR)

import ctypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.GetPixel.restype = ctypes.c_uint
gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_int32
user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetWindowLongW.restype = ctypes.c_int32
user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int32]
user32.SetLayeredWindowAttributes.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_ubyte, ctypes.c_uint32]
user32.PrintWindow.restype = ctypes.c_int
user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         ctypes.c_uint32]
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_uint]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.CreateEllipticRgn.restype = ctypes.c_void_p
gdi32.CreateEllipticRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int]
user32.SetWindowRgn.restype = ctypes.c_int
user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
LWA_COLORKEY = 0x1
LWA_ALPHA = 0x2
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
PW_RENDERFULLCONTENT = 0x02


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def _readback(hdc_mem, hbmp, w, h):
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h          # 负 = top-down，行序与屏幕一致
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0      # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), 0)
    return bytes(buf)


def grab_screen(x, y, w, h):
    """屏幕抓取。注意：抓不到 WebView2（DirectComposition 不在 screen DC 里）。"""
    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY | CAPTUREBLT)
        return _readback(hdc_mem, hbmp, w, h)
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def grab_window(hwnd, w, h):
    """抓窗口自身的渲染结果（PW_RENDERFULLCONTENT 支持 DirectComposition）。"""
    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        return ok, _readback(hdc_mem, hbmp, w, h)
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def sample(buf, w, x, y):
    o = (y * w + x) * 4
    return (buf[o + 2], buf[o + 1], buf[o])


def hexs(c):
    return '#%02X%02X%02X' % c


def write_png(path, w, h, bgra):
    """手写 PNG（zlib + struct 都是标准库，不依赖 PIL）。"""
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        row = bgra[y * w * 4:(y + 1) * w * 4]
        for i in range(0, len(row), 4):
            raw += bytes((row[i + 2], row[i + 1], row[i]))

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(bytes(raw), 6))
           + chunk(b'IEND', b''))
    with open(path, 'wb') as fh:
        fh.write(png)


HTML_TMPL = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:transparent;overflow:hidden;width:100%%;height:100%%}
.ring{position:absolute;left:50%%;top:50%%;width:150px;height:150px;
      margin:-75px 0 0 -75px;border-radius:50%%;background:#FF0000}
.tag{position:absolute;left:0;right:0;top:10px;text-align:center;
     font:bold 24px Consolas,monospace;color:#00C000;letter-spacing:2px}
</style></head><body>
<div class="tag">__VAR__</div><div class="ring"></div></body></html>"""

X, Y, W, H = 300, 200, 300, 300
GX, GY, GW, GH = 150, 100, 640, 560

log = []


def out(s=''):
    log.append(str(s))
    try:
        with open(RESULT, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(log) + '\n')
    except OSError:
        pass


import webview  # noqa: E402

window = webview.create_window(
    'transparency probe', html=HTML_TMPL.replace('__VAR__', VAR.upper()),
    x=X, y=Y, width=W, height=H,
    frameless=True, transparent=True, on_top=True,
    resizable=False, easy_drag=False)


def ui(form, fn):
    """WinForms 控件属性必须在 UI 线程改，否则抛 InvalidOperationException。"""
    from System import Action
    form.Invoke(Action(fn))


def read_prop(form, name):
    from System import Action
    holder = {}
    form.Invoke(Action(lambda: holder.__setitem__('v', getattr(form, name))))
    return holder.get('v')


def worker():
    out('=' * 64)
    out('variant = %s    屏幕 %dx%d' % (VAR, user32.GetSystemMetrics(0),
                                        user32.GetSystemMetrics(1)))
    out('窗口 = (%d,%d) %dx%d' % (X, Y, W, H))
    try:
        time.sleep(2.5)

        from webview.platforms import winforms as wf
        inst = getattr(wf.BrowserView, 'instances', {})
        if not inst:
            out('!! BrowserView.instances 为空，拿不到 Form')
            return
        form = list(inst.values())[-1]
        hwnd = int(form.Handle.ToInt64())
        ex0 = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        out('hwnd = 0x%X   exstyle before = 0x%08X' % (hwnd, ex0 & 0xFFFFFFFF))
        out('BackColor before  = %s' % read_prop(form, 'BackColor'))
        out('TransparencyKey before = %s' % read_prop(form, 'TransparencyKey'))

        if VAR == 'backcolor':
            def apply(f=form):
                from System.Drawing import Color
                f.SetStyle(wf.WinForms.ControlStyles.SupportsTransparentBackColor, True)
                f.BackColor = Color.Transparent
            ui(form, apply)
            out('applied: Form.BackColor = Color.Transparent（不引入 layered，最安全）')
        elif VAR == 'transkey':
            def apply(f=form):
                from System.Drawing import Color
                f.BackColor = Color.FromArgb(255, 0, 255)
                f.TransparencyKey = Color.FromArgb(255, 0, 255)
            ui(form, apply)
            out('applied: Form.BackColor=#FF00FF + TransparencyKey=#FF00FF（WinForms 色彩键）')
        elif VAR == 'colorkey':
            def apply(f=form):
                from System.Drawing import Color
                f.BackColor = Color.FromArgb(255, 0, 255)
            ui(form, apply)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex0 | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(hwnd, 0x00FF00FF, 0, LWA_COLORKEY)
            out('applied: Form.BackColor=#FF00FF + WS_EX_LAYERED + LWA_COLORKEY(#FF00FF)')
        elif VAR == 'layered':
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex0 | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
            out('applied: WS_EX_LAYERED + LWA_ALPHA(255)（只测 layered 本身的影响）')
        elif VAR == 'ellipse':
            hrgn = gdi32.CreateEllipticRgn(0, 0, W, H)
            ok = user32.SetWindowRgn(hwnd, hrgn, True)
            out('applied: SetWindowRgn(椭圆 0,0,%d,%d) ret=%s' % (W, H, ok))
        else:
            out('applied: nothing (baseline —— 复现用户看到的画面)')

        ex1 = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        out('exstyle after  = 0x%08X  (WS_EX_LAYERED=%s)'
            % (ex1 & 0xFFFFFFFF, bool(ex1 & WS_EX_LAYERED)))
        out('BackColor after   = %s' % read_prop(form, 'BackColor'))
        out('TransparencyKey after = %s' % read_prop(form, 'TransparencyKey'))

        time.sleep(1.6)

        # ---- A. 窗口自身渲染（能反映 WebView2 真实内容）----
        out('- A. PrintWindow 抓窗口自身 -')
        try:
            ok, wbuf = grab_window(hwnd, W, H)
            write_png(SHOT_WIN, W, H, wbuf)
            out('  PrintWindow ret = %d   已写 %s' % (ok, os.path.basename(SHOT_WIN)))
            hdc = user32.GetDC(None)
            center = (0, 0, 0)
            user32.ReleaseDC(None, hdc)
            center = sample(wbuf, W, W // 2, H // 2)
            out('  窗口中心(应为红圈#FF0000) = %s' % hexs(center))
            n_red = n_nonblack = 0
            for yy in range(0, H, 6):
                for xx in range(0, W, 6):
                    r, g, b = sample(wbuf, W, xx, yy)
                    if r > 170 and g < 90 and b < 90:
                        n_red += 1
                    if (r, g, b) != (0, 0, 0):
                        n_nonblack += 1
            total = (H // 6) * (W // 6)
            out('  红圈像素 %d/%d  非黑像素 %d/%d' % (n_red, total, n_nonblack, total))
            if n_red > 20:
                out('  => WebView2 内容**渲染出来了**（红圈可见）')
            elif n_nonblack > 20:
                out('  => 有内容但不是红圈，需看图')
            else:
                out('  => WebView2 **完全没渲染**（全黑）')
        except Exception as e:
            out('!! PrintWindow 失败: %r' % (e,))

        # ---- B. 屏幕抓取（看窗口边界 / 底色）----
        out('- B. BitBlt 抓屏幕（窗口区域抓不到 WebView2，仅供参考）----')
        buf = grab_screen(GX, GY, GW, GH)
        write_png(SHOT_SCREEN, GW, GH, buf)
        out('  已写 %s' % os.path.basename(SHOT_SCREEN))

        def at(name, px, py):
            if not (GX <= px < GX + GW and GY <= py < GY + GH):
                out('  %-14s (%4d,%4d) 越界' % (name, px, py))
                return None
            c = sample(buf, GW, px - GX, py - GY)
            out('  %-14s (%4d,%4d) = %s' % (name, px, py, hexs(c)))
            return c

        at('窗口外桌面', X - 60, Y + 60)
        nw = at('窗口内左上', X + 25, Y + 25)
        at('窗口内右下', X + W - 25, Y + H - 25)

        # GetPixel 单独也读一次，供和 BitBlt 对照
        hdc = user32.GetDC(None)
        gp = gdi32.GetPixel(hdc, X + 25, Y + 25)
        user32.ReleaseDC(None, hdc)
        out('  GetPixel 同点 = #%02X%02X%02X'
            % (gp & 0xFF, (gp >> 8) & 0xFF, (gp >> 16) & 0xFF))

        out('- 判定（屏幕通道）----')
        if nw is None:
            out('  越界，看图')
        elif nw == (240, 240, 240):
            out('  窗口内 #F0F0F0 = SystemColors.Control -> **没透**')
        elif nw == (255, 0, 255):
            out('  窗口内 #FF00FF = 我们设的 key 色（没被抠）-> 色彩键无效')
        else:
            out('  窗口内 %s -> 看 tp_*.png 判断' % hexs(nw))
    except Exception:
        out('!! 出错:')
        out(traceback.format_exc())
    finally:
        out('DONE')
        try:
            window.destroy()
        except Exception:
            pass


def hard_kill():
    time.sleep(40)
    out('!! 硬超时，强制退出')
    os._exit(9)


threading.Thread(target=hard_kill, daemon=True).start()
threading.Thread(target=worker, daemon=True).start()

webview.start(debug=False)
