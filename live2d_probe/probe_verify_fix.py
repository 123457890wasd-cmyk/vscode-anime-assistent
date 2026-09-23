# -*- coding: utf-8 -*-
"""验证修复：给一个 pid，检查它的窗口 (1) Mica 背景是否关掉了，(2) 屏幕上是真透明还是实心块。

两件事都要看：
  * DwmGetWindowAttribute(38) 应该 == 1（DWMSBT_NONE）
  * 屏幕 BitBlt：窗口区域与「窗口隐藏后的桌面」一致的像素占比，应该很高

会把窗口先挪到 (--x, --y)，避免和用户正在跑的那个桌宠叠在一起
（叠在一起的话 hide/show 差分就不可信了）。

用法：probe_verify_fix.py --pid 1234 --x 300 --y 250
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_live_fix3 import (shot, png_write, top_colors, pct_equal,  # noqa
                             RECT, RGN_NAMES, u32,
                             SW_HIDE, SW_SHOW, SWP_NOSIZE, SWP_NOZORDER,
                             SWP_NOACTIVATE)

dwm = ctypes.windll.dwmapi
dwm.DwmGetWindowAttribute.restype = ctypes.c_long
dwm.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, wt.DWORD,
                                      ctypes.c_void_p, wt.DWORD]
u32.GetWindowLongW.restype = ctypes.c_long
u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
u32.GetWindowRgn.restype = ctypes.c_int
u32.GetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'verify_fix.txt')
DWMWA_BACKDROP = 38
BACKDROP_NAMES = {0: 'AUTO', 1: 'NONE', 2: 'MAINWINDOW(Mica)', 3: 'TRANSIENT',
                  4: 'TABBED'}


def get_backdrop(hwnd):
    v = ctypes.c_int(-1)
    rc = dwm.DwmGetWindowAttribute(hwnd, DWMWA_BACKDROP, ctypes.byref(v),
                                   ctypes.sizeof(v))
    return (v.value if rc == 0 else -1), rc


def main():
    args = sys.argv[1:]
    pid = int(args[args.index('--pid') + 1]) if '--pid' in args else 0
    mx = int(args[args.index('--x') + 1]) if '--x' in args else 300
    my = int(args[args.index('--y') + 1]) if '--y' in args else 250

    L = ['=' * 74, '验证修复：Mica 关了吗？屏幕真透了吗？', 'pid=%d 移窗到 (%d,%d)'
         % (pid, mx, my), '=' * 74]

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
        r = RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left > 100 and r.bottom - r.top > 100 and target is None:
            target = hwnd
    if not target:
        L.append('没找到窗口（pid=%d 的顶层窗口共 %d 个）' % (pid, len(found)))
        with open(OUT, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(L) + '\n')
        print('written', OUT)
        return

    c2 = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(target, c2, 256)
    bd, rc = get_backdrop(target)
    L.append('hwnd=0x%08X class=%s' % (target, c2.value[:46]))
    L.append('exstyle=0x%08X' % (u32.GetWindowLongW(target, -20) & 0xFFFFFFFF,))
    L.append('>>> DWMWA_SYSTEMBACKDROP_TYPE = %s (%s)   ← 期望 NONE'
             % (bd, BACKDROP_NAMES.get(bd, '?')))
    r = ctypes.c_void_p()
    _rgn = ctypes.windll.gdi32.CreateRectRgn(0, 0, 1, 1)
    had = u32.GetWindowRgn(target, _rgn)
    L.append('window region = %s' % RGN_NAMES.get(had, had))

    # 挪走，别和用户正在跑的那个桌宠重叠
    u32.SetWindowPos(target, None, mx, my, 0, 0,
                     SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(0.8)

    wr = RECT()
    u32.GetWindowRect(target, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    L.append('移动后 window rect = (%d,%d)-(%d,%d) %dx%d'
             % (wr.left, wr.top, wr.right, wr.bottom, w, h))

    u32.ShowWindow(target, SW_HIDE)
    time.sleep(0.45)
    desk = shot(wr.left, wr.top, w, h)
    u32.ShowWindow(target, SW_SHOW)
    u32.SetWindowPos(target, None, mx, my, 0, 0,
                     SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(0.7)
    base = shot(wr.left, wr.top, w, h)
    png_write(os.path.join(HERE, 'verify_fix.png'), w, h, base)

    L.append('')
    L.append('[窗口隐藏=桌面] %s'
             % '  '.join('%s %.1f%%' % c for c in top_colors(desk)))
    L.append('[窗口可见      ] %s'
             % '  '.join('%s %.1f%%' % c for c in top_colors(base)))
    L.append('')
    L.append('>>> 与桌面一致 = %.2f%%   ← 越高 = 越透明（修复前是 0.00%%，修复后 ~90%%）'
             % pct_equal(base, desk))
    L.append('    PNG: verify_fix.png')

    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
