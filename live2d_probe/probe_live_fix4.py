# -*- coding: utf-8 -*-
"""关键实验：关掉 DWM 的 Mica 背景（DWMWA_SYSTEMBACKDROP_TYPE=1），
再看 region 裁剪、颜色键会不会「突然开始生效」。

背景：
  pywebview 的 update_title_bar_theme() 在系统深色模式下会设
      DwmSetWindowAttribute(hwnd, 38, 2)   # DWMSBT_MAINWINDOW == Mica
  而 Mica 是 DWM 自己画的，不在窗口的 GDI 重定向表面里，所以：
      SetWindowRgn 成功但屏幕不变；LWA_COLORKEY 成功但挖不掉；
      只有整窗 LWA_ALPHA 有效（因为它作用在合成结果上）。

用法：probe_live_fix4.py [pid]
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_live_fix3 import (shot, png_write, top_colors, pct_equal,  # noqa
                             RECT, RGN_NAMES, u32, g32,
                             SRCCOPY, SW_HIDE, SW_SHOW, SWP_NOSIZE, SWP_NOMOVE,
                             SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED,
                             GWL_EXSTYLE, WS_EX_LAYERED, LWA_COLORKEY, LWA_ALPHA,
                             RDW_INVALIDATE, RDW_UPDATENOW, RDW_ALLCHILDREN)

dwm = ctypes.windll.dwmapi
dwm.DwmSetWindowAttribute.restype = ctypes.c_long
dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, wt.DWORD,
                                      ctypes.c_void_p, wt.DWORD]
dwm.DwmGetWindowAttribute.restype = ctypes.c_long
dwm.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, wt.DWORD,
                                      ctypes.c_void_p, wt.DWORD]
u32.SetWindowRgn.restype = ctypes.c_int
u32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
u32.GetWindowRgn.restype = ctypes.c_int
u32.GetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.CombineRgn.restype = ctypes.c_int
g32.CombineRgn.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int]
g32.CreateRectRgn.restype = ctypes.c_void_p
g32.CreateRectRgn.argtypes = [ctypes.c_int] * 4

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'live_fix4.txt')
DWMWA_BACKDROP = 38
DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2


def dwm_get(hwnd, attr):
    v = ctypes.c_int(-1)
    rc = dwm.DwmGetWindowAttribute(hwnd, attr, ctypes.byref(v),
                                   ctypes.sizeof(v))
    return rc, v.value


def dwm_set(hwnd, attr, val):
    v = ctypes.c_int(val)
    return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v),
                                     ctypes.sizeof(v))


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 44164
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
        print('no window')
        return

    wr = RECT()
    u32.GetWindowRect(target, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    x0, y0 = wr.left, wr.top
    L = ['=' * 74, '关掉 Mica 之后 region / 颜色键会不会生效？',
         'target=0x%08X %dx%d at (%d,%d)' % (target, w, h, x0, y0),
         '=' * 74]
    rc, before = dwm_get(target, DWMWA_BACKDROP)
    L.append('DwmGetWindowAttribute(38 backdrop) 改前: rc=%s val=%s' % (rc, before))

    # 桌面参考
    u32.ShowWindow(target, SW_HIDE)
    time.sleep(0.45)
    desk = shot(x0, y0, w, h)
    u32.ShowWindow(target, SW_SHOW)
    time.sleep(0.45)
    base = shot(x0, y0, w, h)
    png_write(os.path.join(HERE, 'fix4_baseline.png'), w, h, base)
    L.append('[baseline   ] %s  与桌面一致=%.2f%%'
             % ('  '.join('%s %.1f%%' % c for c in top_colors(base)),
                pct_equal(base, desk)))

    def snap(tag, png):
        time.sleep(0.6)
        b = shot(x0, y0, w, h)
        png_write(os.path.join(HERE, png), w, h, b)
        L.append('[%-11s] %s  与桌面一致=%.2f%%'
                 % (tag, '  '.join('%s %.1f%%' % c for c in top_colors(b)),
                    pct_equal(b, desk)))

    def bump():
        u32.SetWindowPos(target, None, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                         | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        u32.RedrawWindow(target, None, None,
                         RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)

    # 保存原 region
    saved = g32.CreateRectRgn(0, 0, 1, 1)
    had = u32.GetWindowRgn(target, saved)
    L.append('原 region = %s' % RGN_NAMES.get(had, had))

    # ---- 1) 只关 Mica ----
    r = dwm_set(target, DWMWA_BACKDROP, DWMSBT_NONE)
    bump()
    L.append('DwmSetWindowAttribute(38, NONE=1) -> %s' % r)
    snap('Mica OFF', 'fix4_micaoff.png')
    L.append('  (改后 backdrop=%s)' % (dwm_get(target, DWMWA_BACKDROP)[1],))

    # ---- 2) Mica 关掉 + region=左上 1/4 ----
    quad = g32.CreateRectRgn(0, 0, w // 2, h // 2)
    u32.SetWindowRgn(target, quad, True)
    bump()
    snap('+region 1/4', 'fix4_micaoff_region.png')

    # ---- 3) Mica 关掉 + region 还原 ----
    tmp = g32.CreateRectRgn(0, 0, 1, 1)
    g32.CombineRgn(tmp, saved, saved, 5)   # RGN_COPY
    u32.SetWindowRgn(target, tmp, True)
    bump()
    snap('+region 原形状', 'fix4_micaoff_regionorig.png')

    # ---- 4) 还原 Mica ----
    dwm_set(target, DWMWA_BACKDROP, before if before >= 0 else DWMSBT_MAINWINDOW)
    bump()
    snap('Mica 还原', 'fix4_restored.png')
    L.append('最终 backdrop=%s  region=%s'
             % (dwm_get(target, DWMWA_BACKDROP)[1],
                RGN_NAMES.get(u32.GetWindowRgn(target, saved), '?')))
    L.append('')
    L.append('判读：若 [Mica OFF] 这一行仍铺满 #202020 ⇒ 底色不是 Mica；')
    L.append('      若 [+region 1/4] 与桌面一致明显上升 ⇒ 关掉 Mica 后 region 裁剪生效。')

    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
