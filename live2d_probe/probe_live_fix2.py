# -*- coding: utf-8 -*-
"""在活窗口上试两种「让窗口真变透明」的机制，测完立刻还原。

判据：屏幕 BitBlt 的像素里，有多少和「窗口隐藏时的桌面」一致 —— 那才是真透明。

  R1: SetWindowRgn 之后再强推一次 SWP_FRAMECHANGED + RedrawWindow
      （之前 SetWindowRgn 读回 COMPLEXREGION 138x220，屏幕却毫无变化，
        怀疑是 region 设上了但 DWM 没重算）
  L1: WS_EX_LAYERED + SetLayeredWindowAttributes(LWA_COLORKEY, #202020)
      —— pywebview 的透明分支从不设 Form.BackColor，
         所以 Form 一直在刷 SystemColors.Control（深色模式就是 #202020）。
         颜色键正好可以把这个「本该透明」的颜色挖掉。

每个状态都存 PNG，最后会还原成原样。

用法：probe_live_fix2.py [pid]
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
OUT = os.path.join(HERE, 'live_fix2.txt')

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
u32.SetWindowLongW.restype = ctypes.c_long
u32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
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
g32.CreateCompatibleDC.restype = ctypes.c_void_p
g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
g32.CreateDIBSection.restype = ctypes.c_void_p
g32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
g32.CreateRectRgn.restype = ctypes.c_void_p
g32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
g32.CombineRgn.restype = ctypes.c_int
g32.CombineRgn.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int]
g32.SelectObject.restype = ctypes.c_void_p
g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.DeleteObject.argtypes = [ctypes.c_void_p]
g32.DeleteDC.argtypes = [ctypes.c_void_p]
g32.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p] \
    + [ctypes.c_int] * 2 + [ctypes.c_uint]

SRCCOPY = 0x00CC0020
RGN_COPY = 5
SW_HIDE, SW_SHOW = 0, 5
SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0004, 0x0010, 0x0020
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
LWA_COLORKEY, LWA_ALPHA = 0x00000001, 0x00000002
RDW_INVALIDATE, RDW_UPDATENOW, RDW_ALLCHILDREN = 0x0001, 0x0100, 0x0080
KEY = 0x00202020          # COLORREF = 0x00BBGGRR -> #202020
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


def top_colors(buf, n=6):
    cnt = Counter()
    for i in range(0, len(buf), 4):
        cnt[(buf[i + 2], buf[i + 1], buf[i])] += 1
    tot = len(buf) // 4
    return [('#%02X%02X%02X' % c, v, round(v * 100.0 / tot, 2))
            for c, v in cnt.most_common(n)]


def pct_equal(a, b):
    """a 里有多少比例的像素和 b 完全一致 —— 对「看得见桌面」来说这就是透明率。"""
    n = min(len(a), len(b)) // 4
    same = 0
    for i in range(n):
        o = i * 4
        if a[o] == b[o] and a[o + 1] == b[o + 1] and a[o + 2] == b[o + 2]:
            same += 1
    return round(same * 100.0 / max(n, 1), 2)


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 44164
    lines = ['=' * 74,
             '活窗口透明机制实测（测完会还原）',
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
    lines.append('该 pid 下顶层窗口 %d 个：' % len(found))
    target = None
    for hwnd in found:
        wr = RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(wr))
        c2 = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, c2, 256)
        ww, wh = wr.right - wr.left, wr.bottom - wr.top
        lines.append('  hwnd=0x%08X %dx%d at (%d,%d) %s'
                     % (hwnd, ww, wh, wr.left, wr.top, c2.value[:44]))
        if ww > 100 and wh > 100 and target is None:
            target = hwnd
    if not target:
        lines.append('没找到目标窗口')
        open(OUT, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
        return

    wr = RECT()
    u32.GetWindowRect(target, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    x0, y0 = wr.left, wr.top
    orig_ex = u32.GetWindowLongW(target, GWL_EXSTYLE) & 0xFFFFFFFF
    lines.append('target=0x%08X  %dx%d at (%d,%d)  exstyle=0x%08X'
                 % (target, w, h, x0, y0, orig_ex))

    # 保存原 region，方便还原
    saved_rgn = g32.CreateRectRgn(0, 0, 1, 1)
    had_rgn = u32.GetWindowRgn(target, saved_rgn)
    lines.append('原 region = %s' % RGN_NAMES.get(had_rgn, had_rgn))

    # 桌面参考：把窗口藏起来抓一张
    u32.ShowWindow(target, SW_HIDE)
    time.sleep(0.45)
    desk = shot(x0, y0, w, h)
    u32.ShowWindow(target, SW_SHOW)
    time.sleep(0.45)
    base = shot(x0, y0, w, h)
    png_write(os.path.join(HERE, 'fix2_baseline.png'), w, h, base)
    lines.append('')
    lines.append('桌面参考(窗口隐藏) 主色: %s'
                 % '  '.join('%s %.1f%%' % (k, p) for k, _, p in top_colors(desk)))
    lines.append('[baseline  ] 主色: %s'
                 % '  '.join('%s %.1f%%' % (k, p) for k, _, p in top_colors(base)))
    lines.append('[baseline  ] 与桌面一致 = %.2f%%  <- 现在几乎全不透明' % pct_equal(base, desk))

    # ---------------- R1: region + 强推帧变化 ----------------
    quad = g32.CreateRectRgn(0, 0, w // 2, h // 2)
    r = u32.SetWindowRgn(target, quad, True)
    u32.SetWindowPos(target, None, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                     | SWP_FRAMECHANGED)
    u32.RedrawWindow(target, None, None,
                     RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
    time.sleep(0.6)
    r1 = shot(x0, y0, w, h)
    png_write(os.path.join(HERE, 'fix2_region.png'), w, h, r1)
    lines.append('')
    lines.append('--- R1: region=左上 1/4 + SWP_FRAMECHANGED ---')
    lines.append('  SetWindowRgn=%s' % r)
    lines.append('  主色: %s' % '  '.join('%s %.1f%%' % (k, p)
                                          for k, _, p in top_colors(r1)))
    lines.append('  与桌面一致 = %.2f%%' % pct_equal(r1, desk))

    # 还原 region
    tmp = g32.CreateRectRgn(0, 0, 1, 1)
    g32.CombineRgn(tmp, saved_rgn, saved_rgn, RGN_COPY)
    u32.SetWindowRgn(target, tmp, True)
    u32.SetWindowPos(target, None, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                     | SWP_FRAMECHANGED)
    time.sleep(0.5)

    # ---------------- L1: layered 颜色键 ----------------
    u32.SetWindowLongW(target, GWL_EXSTYLE, orig_ex | WS_EX_LAYERED)
    ok = u32.SetLayeredWindowAttributes(target, KEY, 0, LWA_COLORKEY)
    u32.SetWindowPos(target, None, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                     | SWP_FRAMECHANGED)
    u32.RedrawWindow(target, None, None,
                     RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
    time.sleep(0.7)
    l1 = shot(x0, y0, w, h)
    png_write(os.path.join(HERE, 'fix2_layered.png'), w, h, l1)
    lines.append('')
    lines.append('--- L1: WS_EX_LAYERED + LWA_COLORKEY(#202020) ---')
    lines.append('  SetLayeredWindowAttributes=%s' % ok)
    lines.append('  主色: %s' % '  '.join('%s %.1f%%' % (k, p)
                                          for k, _, p in top_colors(l1)))
    lines.append('  与桌面一致 = %.2f%%  <- 越高越透明' % pct_equal(l1, desk))

    # 还原 layered
    u32.SetLayeredWindowAttributes(target, 0, 255, LWA_ALPHA)
    u32.SetWindowLongW(target, GWL_EXSTYLE, orig_ex)
    u32.SetWindowPos(target, None, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                     | SWP_FRAMECHANGED)
    u32.RedrawWindow(target, None, None,
                     RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
    time.sleep(0.6)
    back = shot(x0, y0, w, h)
    png_write(os.path.join(HERE, 'fix2_restored.png'), w, h, back)
    lines.append('')
    lines.append('--- 还原之后 ---')
    lines.append('  exstyle=0x%08X  (原 0x%08X)  region=%s'
                 % (u32.GetWindowLongW(target, GWL_EXSTYLE) & 0xFFFFFFFF,
                    orig_ex, RGN_NAMES.get(u32.GetWindowRgn(target, saved_rgn), '?')))
    lines.append('  主色: %s' % '  '.join('%s %.1f%%' % (k, p)
                                          for k, _, p in top_colors(back)))
    lines.append('')
    lines.append('  PNG: fix2_baseline / fix2_region / fix2_layered / fix2_restored')

    text = '\n'.join(lines)
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
