# -*- coding: utf-8 -*-
"""把「窗口形状/分层改动到底有没有生效」这件事本身测出来。

关键判别：把整个窗口用 LWA_ALPHA 设成 alpha=0（应该彻底消失）。
  - 窗口真的消失了 ⇒ 分层机制有效 ⇒ 那么 LWA_COLORKEY(#202020) 无效，
    说明那块 #202020 不在窗口自己的合成表面里（是别的层画的）
  - 窗口没消失     ⇒ 分层改动对这个窗口根本不起作用（更离奇，要继续查）

另外顺手把这些环境事实记下来，避免再猜：
  DPI 感知、每窗口 DPI、窗口矩形 vs 屏幕矩形、每个子窗口的 region、
  DwmGetWindowAttribute(cloaked / backdrop)、逐点 WindowFromPoint。

用法：probe_live_fix3.py [pid]
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import time
import zlib
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'live_fix3.txt')

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
dwm = ctypes.windll.dwmapi

u32.GetDC.restype = ctypes.c_void_p
u32.GetDC.argtypes = [ctypes.c_void_p]
u32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.EnumChildWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_void_p]
u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowLongW.restype = ctypes.c_long
u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.SetWindowLongW.restype = ctypes.c_long
u32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 5
u32.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_uint]
u32.SetWindowRgn.restype = ctypes.c_int
u32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
u32.GetWindowRgn.restype = ctypes.c_int
u32.GetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.SetLayeredWindowAttributes.restype = ctypes.c_int
u32.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, wt.DWORD,
                                           ctypes.c_ubyte, wt.DWORD]
u32.GetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p]
u32.WindowFromPoint.restype = ctypes.c_void_p
u32.WindowFromPoint.argtypes = [wt.POINT]
u32.GetAncestor.restype = ctypes.c_void_p
u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
g32.GetRgnBox.restype = ctypes.c_int
g32.GetRgnBox.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetDpiForWindow.restype = ctypes.c_uint
u32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
u32.GetSystemMetrics.argtypes = [ctypes.c_int]
dwm.DwmGetWindowAttribute.restype = ctypes.c_long
g32.CreateCompatibleDC.restype = ctypes.c_void_p
g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
g32.CreateDIBSection.restype = ctypes.c_void_p
g32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
g32.CreateRectRgn.restype = ctypes.c_void_p
g32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
g32.SelectObject.restype = ctypes.c_void_p
g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.DeleteObject.argtypes = [ctypes.c_void_p]
g32.DeleteDC.argtypes = [ctypes.c_void_p]
g32.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p] \
    + [ctypes.c_int] * 2 + [ctypes.c_uint]

SRCCOPY = 0x00CC0020
SW_HIDE, SW_SHOW = 0, 5
SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0004, 0x0010, 0x0020
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
LWA_COLORKEY, LWA_ALPHA = 0x00000001, 0x00000002
RDW_INVALIDATE, RDW_UPDATENOW, RDW_ALLCHILDREN = 0x0001, 0x0100, 0x0080
SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_SYSTEMBACKDROP_TYPE = 38
RGN_NAMES = {0: 'ERROR/NO-REGION', 1: 'NULLREGION', 2: 'SIMPLEREGION',
             3: 'COMPLEXREGION'}


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', wt.WORD),
                ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wt.DWORD * 3)]


def _chunk(tag, data):
    return (struct.pack('>I', len(data)) + tag + data +
            struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))


def png_write(path, w, h, buf):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        base = y * w * 4
        for x in range(w):
            o = base + x * 4
            raw += bytes((buf[o + 2], buf[o + 1], buf[o], 255))
    with open(path, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n')
        fh.write(_chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)))
        fh.write(_chunk(b'IDAT', zlib.compress(bytes(raw), 6)))
        fh.write(_chunk(b'IEND', b''))


def shot(x, y, w, h):
    hdc = u32.GetDC(None)
    mem = g32.CreateCompatibleDC(hdc)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bits = ctypes.c_void_p()
    hbm = g32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits),
                               None, 0)
    g32.SelectObject(mem, hbm)
    g32.BitBlt(mem, 0, 0, w, h, hdc, x, y, SRCCOPY)
    buf = ctypes.string_at(bits, w * h * 4)
    g32.DeleteObject(hbm)
    g32.DeleteDC(mem)
    u32.ReleaseDC(None, hdc)
    return buf


def top_colors(buf, n=4):
    cnt = Counter()
    for i in range(0, len(buf), 4):
        cnt[(buf[i + 2], buf[i + 1], buf[i])] += 1
    tot = len(buf) // 4
    return [('#%02X%02X%02X' % c, round(v * 100.0 / tot, 2))
            for c, v in cnt.most_common(n)]


def pct_equal(a, b):
    n = min(len(a), len(b)) // 4
    same = 0
    for i in range(n):
        o = i * 4
        if a[o] == b[o] and a[o + 1] == b[o + 1] and a[o + 2] == b[o + 2]:
            same += 1
    return round(same * 100.0 / max(n, 1), 2)


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 44164
    L = ['=' * 74,
         '窗口形状/分层改动到底有没有生效？',
         'pid=%d' % pid,
         '=' * 74]

    L.append('屏幕: primary=%dx%d virtual=(%d,%d) %dx%d'
             % (u32.GetSystemMetrics(SM_CXSCREEN),
                u32.GetSystemMetrics(SM_CYSCREEN),
                u32.GetSystemMetrics(SM_XVIRTUALSCREEN),
                u32.GetSystemMetrics(SM_YVIRTUALSCREEN),
                u32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
                u32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))

    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, lp):
        wpid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid:
            found.append(hwnd)
        return True

    u32.EnumWindows(cb, 0)
    target = None
    for hwnd in found:
        wr = RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(wr))
        if wr.right - wr.left > 100 and wr.bottom - wr.top > 100 and target is None:
            target = hwnd
    if not target:
        L.append('没找到目标窗口')
        open(OUT, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
        return

    wr = RECT()
    u32.GetWindowRect(target, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    x0, y0 = wr.left, wr.top
    orig_ex = u32.GetWindowLongW(target, GWL_EXSTYLE) & 0xFFFFFFFF
    L.append('target=0x%08X  %dx%d at (%d,%d)  dpi=%d  exstyle=0x%08X'
             % (target, w, h, x0, y0, u32.GetDpiForWindow(target), orig_ex))

    for attr, name in ((DWMWA_CLOAKED, 'cloaked'),
                       (DWMWA_SYSTEMBACKDROP_TYPE, 'backdrop'),
                       (DWMWA_EXTENDED_FRAME_BOUNDS, 'ext_frame')):
        v = ctypes.c_int(-1)
        rc = dwm.DwmGetWindowAttribute(target, attr, ctypes.byref(v),
                                       ctypes.sizeof(v))
        L.append('  DwmGetWindowAttribute(%s) rc=%s val=%s'
                 % (name, rc, v.value))

    L.append('')
    L.append('--- 逐点 WindowFromPoint（谁在最上面） ---')
    for dx, dy in ((20, 20), (140, 220), (140, 390)):
        pt = wt.POINT(x0 + dx, y0 + dy)
        hw = u32.WindowFromPoint(pt)
        c2 = ctypes.create_unicode_buffer(256)
        if hw:
            u32.GetClassNameW(hw, c2, 256)
        owner = '?'
        if hw and hw != target:
            root = u32.GetAncestor(hw, 2)   # GA_ROOT = 2
            owner = 'root=0x%08X %s' % (root or 0,
                                        'same' if root == target else 'OTHER')
        L.append('  (%d,%d) -> hwnd=0x%08X class=%s  %s'
                 % (x0 + dx, y0 + dy, hw or 0, c2.value[:40], owner))

    L.append('')
    L.append('--- 子窗口及其 region ---')
    kids = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def kcb(h, lp):
        kids.append(h)
        return True

    u32.EnumChildWindows(target, kcb, 0)
    for i, k in enumerate(kids):
        c2 = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(k, c2, 256)
        r = g32.CreateRectRgn(0, 0, 1, 1)
        t = u32.GetWindowRgn(k, r)
        box = RECT()
        g32.GetRgnBox(r, ctypes.byref(box))
        L.append('  kid#%d 0x%08X %-44s ex=0x%08X region=%s bbox=%s'
                 % (i, k, c2.value[:44],
                    u32.GetWindowLongW(k, GWL_EXSTYLE) & 0xFFFFFFFF,
                    RGN_NAMES.get(t, t),
                    '(%d,%d)-(%d,%d)' % (box.left, box.top, box.right, box.bottom)))
        g32.DeleteObject(r)

    # ---- 桌面参考 ----
    u32.ShowWindow(target, SW_HIDE)
    time.sleep(0.45)
    desk = shot(x0, y0, w, h)
    u32.ShowWindow(target, SW_SHOW)
    time.sleep(0.45)
    base = shot(x0, y0, w, h)
    png_write(os.path.join(HERE, 'fix3_baseline.png'), w, h, base)
    L.append('')
    L.append('[baseline] %s  与桌面一致=%.2f%%'
             % ('  '.join('%s %.1f%%' % c for c in top_colors(base)),
                pct_equal(base, desk)))

    def apply_mark(tag, png, setter):
        setter()
        u32.SetWindowPos(target, None, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                         | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        u32.RedrawWindow(target, None, None,
                         RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
        time.sleep(0.7)
        b = shot(x0, y0, w, h)
        png_write(os.path.join(HERE, png), w, h, b)
        key = wt.DWORD()
        alpha = ctypes.c_ubyte()
        flags = wt.DWORD()
        u32.GetLayeredWindowAttributes(target, ctypes.byref(key),
                                       ctypes.byref(alpha), ctypes.byref(flags))
        L.append('[%s] %s  与桌面一致=%.2f%%  (layered key=0x%06X a=%d f=%d)'
                 % (tag, '  '.join('%s %.1f%%' % c for c in top_colors(b)),
                    pct_equal(b, desk), key.value, alpha.value, flags.value))
        return b

    L.append('')
    L.append('=== 判别实验 ===')
    mark = [False]

    def mk():
        u32.SetWindowLongW(target, GWL_EXSTYLE, orig_ex | WS_EX_LAYERED)

    apply_mark('L0 layered+alpha', 'fix3_a0.png',
               lambda: (mk(), u32.SetLayeredWindowAttributes(target, 0, 0,
                                                             LWA_ALPHA)))
    apply_mark('L1 key=#FFFFFF ', 'fix3_keywhite.png',
               lambda: u32.SetLayeredWindowAttributes(target, 0x00FFFFFF, 0,
                                                      LWA_COLORKEY))
    apply_mark('L2 key=#202020 ', 'fix3_key202020.png',
               lambda: u32.SetLayeredWindowAttributes(target, 0x00202020, 0,
                                                      LWA_COLORKEY))
    apply_mark('L3 layered+alpha255', 'fix3_a255.png',
               lambda: u32.SetLayeredWindowAttributes(target, 0, 255,
                                                      LWA_ALPHA))

    # 还原
    u32.SetLayeredWindowAttributes(target, 0, 255, LWA_ALPHA)
    u32.SetWindowLongW(target, GWL_EXSTYLE, orig_ex)
    u32.SetWindowPos(target, None, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                     | SWP_FRAMECHANGED)
    u32.RedrawWindow(target, None, None,
                     RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
    time.sleep(0.6)
    back = shot(x0, y0, w, h)
    png_write(os.path.join(HERE, 'fix3_restored.png'), w, h, back)
    L.append('[restored] %s  与桌面一致=%.2f%%  exstyle=0x%08X'
             % ('  '.join('%s %.1f%%' % c for c in top_colors(back)),
                pct_equal(back, desk),
                u32.GetWindowLongW(target, GWL_EXSTYLE) & 0xFFFFFFFF))
    L.append('')
    L.append('读法：L0 若「与桌面一致」跳到 ~100%% ⇒ 分层生效；仍为 0%% ⇒ 分层对这个窗口无效。')

    text = '\n'.join(L)
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
