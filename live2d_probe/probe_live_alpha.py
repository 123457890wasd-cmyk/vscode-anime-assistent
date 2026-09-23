# -*- coding: utf-8 -*-
"""判定题：屏幕上那块 #202020 是**桌宠自己画的**，还是**桌面透过来的**？

方法：隐藏窗口前后各抓一次屏幕同一个矩形。
  A = 窗口可见时的屏幕内容
  B = 窗口隐藏后的屏幕内容（= 窗口后面的东西，也就是桌面）
  - A 与 B 一致  ⇒ 窗口是透明的，我们看到的 #202020 其实是桌面 → 透明其实已经生效
  - A 与 B 不同，且 A 是整片 #202020 ⇒ 那块底色是窗口自己画的（Form 背景）

顺带存 PNG，我可以用眼睛复核。

用法：probe_live_alpha.py [pid]
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
OUT = os.path.join(HERE, 'live_alpha.txt')

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32

u32.GetDC.restype = ctypes.c_void_p
u32.GetDC.argtypes = [ctypes.c_void_p]
u32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowLongW.restype = ctypes.c_long
u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
u32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
# SetWindowPos(hWnd, hWndInsertAfter, X, Y, cx, cy, uFlags) —— 共 7 个参数
# （两个句柄 + 4 个 int + flags），多写一个 int 就会报 "takes 8 (7 given)"。
u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 5
g32.CreateCompatibleDC.restype = ctypes.c_void_p
g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
g32.CreateDIBSection.restype = ctypes.c_void_p
g32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
g32.SelectObject.restype = ctypes.c_void_p
g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.DeleteObject.argtypes = [ctypes.c_void_p]
g32.DeleteDC.argtypes = [ctypes.c_void_p]
# BitBlt(hdcDest, x, y, cx, cy, hdcSrc, x1, y1, rop) —— 共 9 个参数：
# dest 之后是 4 个 int（不是 5 个），写成 5 个会报
# "this function takes 10 arguments (9 given)"。
g32.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p] \
    + [ctypes.c_int] * 2 + [ctypes.c_uint]

SRCCOPY = 0x00CC0020
SW_HIDE = 0
SW_SHOW = 5
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
GWL_EXSTYLE = -20


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
    """buf = top-down BGRA，贴 PNG 时写死 alpha=255（我们看的是屏幕真实颜色）。"""
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


def top_colors(buf, n=6):
    cnt = Counter()
    for i in range(0, len(buf), 4):
        cnt[(buf[i + 2], buf[i + 1], buf[i])] += 1
    tot = len(buf) // 4
    return [('#%02X%02X%02X' % c, v, round(v * 100.0 / tot, 2))
            for c, v in cnt.most_common(n)]


def diff_pct(a, b):
    n = min(len(a), len(b)) // 4
    same = 0
    for i in range(n):
        o = i * 4
        if a[o] == b[o] and a[o + 1] == b[o + 1] and a[o + 2] == b[o + 2]:
            same += 1
    return round((n - same) * 100.0 / max(n, 1), 2)


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 44164
    lines = ['=' * 74,
             '判定：屏幕上的 #202020 是窗口画的，还是桌面透过来的？',
             'pid=%d' % pid,
             '=' * 74]

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
        if wr.right - wr.left > 100 and wr.bottom - wr.top > 100:
            target = hwnd
            break
    if not target:
        lines.append('没找到桌宠顶层窗口')
    else:
        wr = RECT()
        u32.GetWindowRect(target, ctypes.byref(wr))
        cr = RECT()
        u32.GetClientRect(target, ctypes.byref(cr))
        ex = u32.GetWindowLongW(target, GWL_EXSTYLE) & 0xFFFFFFFF
        c2 = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(target, c2, 256)
        lines.append('hwnd    = 0x%08X  class=%s' % (target, c2.value))
        lines.append('window  = (%d,%d)-(%d,%d) %dx%d'
                     % (wr.left, wr.top, wr.right, wr.bottom,
                        wr.right - wr.left, wr.bottom - wr.top))
        lines.append('client  = %dx%d' % (cr.right - cr.left, cr.bottom - cr.top))
        lines.append('exstyle = 0x%08X  layered=%s  visible=%s'
                     % (ex, bool(ex & 0x00080000), bool(u32.IsWindowVisible(target))))

        # 抓大一圈，带上下文
        M = 70
        x0, y0 = wr.left - M, wr.top - M
        w, h = (wr.right - wr.left) + 2 * M, (wr.bottom - wr.top) + 2 * M

        a = shot(x0, y0, w, h)
        png_write(os.path.join(HERE, 'alpha_visible.png'), w, h, a)

        u32.ShowWindow(target, SW_HIDE)
        time.sleep(0.45)
        b = shot(x0, y0, w, h)
        png_write(os.path.join(HERE, 'alpha_hidden.png'), w, h, b)

        u32.ShowWindow(target, SW_SHOW)
        u32.SetWindowPos(target, None, wr.left, wr.top, 0, 0,
                         SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        time.sleep(0.45)
        c = shot(x0, y0, w, h)
        png_write(os.path.join(HERE, 'alpha_visible2.png'), w, h, c)

        lines.append('')
        lines.append('抓取区域 (%d,%d) %dx%d（含 %d px 外扩上下文）'
                     % (x0, y0, w, h, M))
        lines.append('  A 窗口可见 : %s' % '  '.join('%s %.1f%%' % (k, p)
                                                     for k, _, p in top_colors(a)))
        lines.append('  B 窗口隐藏 : %s' % '  '.join('%s %.1f%%' % (k, p)
                                                     for k, _, p in top_colors(b)))
        lines.append('  C 再次显示 : %s' % '  '.join('%s %.1f%%' % (k, p)
                                                     for k, _, p in top_colors(c)))
        lines.append('')
        lines.append('  A vs B 不同像素占比 = %.2f%%   （≈0 表示窗口完全没画东西）'
                     % diff_pct(a, b))
        lines.append('  C vs A 不同像素占比 = %.2f%%   （看显示/隐藏有没有留下副作用）'
                     % diff_pct(c, a))
        lines.append('')
        lines.append('  PNG: alpha_visible.png / alpha_hidden.png / alpha_visible2.png')

    text = '\n'.join(lines)
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
