# -*- coding: utf-8 -*-
"""在**正在运行的**桌宠窗口上做实验：把父窗口的 region 复制给子窗口，看屏幕变不变。

要验证的假设（由 live_window.txt 的证据推出来）：
  SetWindowRgn 作用在顶层窗口上，GetWindowRgn 读回 138x220 已证明它生效；
  但屏幕截图里那块铺满 284x441 的 #202020（== SystemColors.Control）没被裁掉。
  同时：顶层 + WinForms 子窗口 + Chromium 子窗口的 PrintWindow 都是 #202020，
  而 "Intermediate D3D Window"（DirectComposition 那层）是 #000000。
  ⇒ 铺底的是 GDI 层（WinForms/Chromium 的窗口底色），而**父窗口的 region 不裁子窗口**。
  ⇒ 把同一个 region 也设到子窗口上，屏幕上的方块就应该消失。

判据用屏幕 BitBlt 读回来的主色 —— 不看 PrintWindow（它不体现 region 裁剪）。

用法：probe_live_fix.py <pid> [--apply]   （不带 --apply 只测量，不改任何东西）
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'live_fix.txt')

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32

u32.GetWindowDC.restype = ctypes.c_void_p
u32.GetDC.restype = ctypes.c_void_p
u32.GetDC.argtypes = [ctypes.c_void_p]
u32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.EnumChildWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowRgn.restype = ctypes.c_int
u32.GetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.SetWindowRgn.restype = ctypes.c_int
u32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
u32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
g32.CreateCompatibleDC.restype = ctypes.c_void_p
g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
g32.CreateDIBSection.restype = ctypes.c_void_p
g32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_uint, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_uint]
g32.CreateRectRgn.restype = ctypes.c_void_p
g32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
g32.CombineRgn.restype = ctypes.c_int
g32.CombineRgn.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int]
g32.GetRgnBox.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.SelectObject.restype = ctypes.c_void_p
g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.DeleteObject.argtypes = [ctypes.c_void_p]
g32.DeleteDC.argtypes = [ctypes.c_void_p]
g32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                       ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                       ctypes.c_int, ctypes.c_int, ctypes.c_uint]
u32.GetSysColor.restype = wt.DWORD
u32.GetSysColor.argtypes = [ctypes.c_int]

RGN_COPY = 5
RGN_NAMES = {0: 'ERROR/NO-REGION', 1: 'NULLREGION', 2: 'SIMPLEREGION',
             3: 'COMPLEXREGION'}
SRCCOPY = 0x00CC0020
COLOR_BTNFACE = 15


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


def screen_shot(x, y, w, h):
    """BitBlt 抓屏幕矩形 —— 这才是用户眼睛看到的东西。"""
    hdc = u32.GetDC(None)
    mem = g32.CreateCompatibleDC(hdc)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负数 = top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbm = g32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits),
                               None, 0)
    g32.SelectObject(mem, hbm)
    ok = g32.BitBlt(mem, 0, 0, w, h, hdc, x, y, SRCCOPY)
    buf = ctypes.string_at(bits, w * h * 4)
    g32.DeleteObject(hbm); g32.DeleteDC(mem); u32.ReleaseDC(None, hdc)
    return ok, buf


def top_colors(buf, n=5):
    cnt = Counter()
    for i in range(0, len(buf), 4):
        cnt[(buf[i + 2], buf[i + 1], buf[i])] += 1
    tot = len(buf) // 4
    return [(('#%02X%02X%02X' % c), v, round(v * 100.0 / tot, 2))
            for c, v in cnt.most_common(n)]


def kids_of(hwnd):
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, l):
        out.append(h)
        return True

    u32.EnumChildWindows(hwnd, cb, 0)
    return out


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 44164
    apply_fix = '--apply' in sys.argv

    lines = ['=' * 74,
             '活窗口实验：把 region 复制到子窗口，屏幕方块会消失吗？',
             'pid=%d  apply=%s' % (pid, apply_fix),
             '=' * 74]

    # 顺手确认一下 #202020 到底是不是 SystemColors.Control
    btnface = u32.GetSysColor(COLOR_BTNFACE)
    r = btnface & 0xFF
    g = (btnface >> 8) & 0xFF
    b = (btnface >> 16) & 0xFF
    lines.append('GetSysColor(COLOR_BTNFACE) = #%02X%02X%02X' % (r, g, b))
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r'Software\Microsoft\Windows\CurrentVersion'
                           r'\Themes\Personalize')
        v, _ = winreg.QueryValueEx(k, 'AppsUseLightTheme')
        lines.append('AppsUseLightTheme = %s  (0=深色)' % v)
    except Exception as exc:
        lines.append('AppsUseLightTheme 读不到: %r' % (exc,))

    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, lparam):
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
        lines.append('没找到像桌宠的顶层窗口')
        open(OUT, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
        return

    wr = RECT()
    u32.GetWindowRect(target, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    lines.append('窗口 screen rect = (%d,%d)-(%d,%d) %dx%d'
                 % (wr.left, wr.top, wr.right, wr.bottom, w, h))

    # 父窗口现有 region 的副本
    parent_rgn = g32.CreateRectRgn(0, 0, 1, 1)
    ptype = u32.GetWindowRgn(target, parent_rgn)
    box = RECT()
    if ptype:
        g32.GetRgnBox(parent_rgn, ctypes.byref(box))
        lines.append('父窗口 region = %s bbox=(%d,%d)-(%d,%d)'
                     % (RGN_NAMES.get(ptype), box.left, box.top,
                        box.right, box.bottom))
    lines.append('')

    def measure(tag):
        ok, buf = screen_shot(wr.left, wr.top, w, h)
        lines.append('  [%s] BitBlt=%s  主色: %s'
                     % (tag, ok, '  '.join('%s %.1f%%' % (c, p)
                                           for c, _, p in top_colors(buf))))

    lines.append('--- 屏幕实测 ---')
    measure('baseline 改之前')

    if not ptype:
        lines.append('父窗口没有 region，实验无法进行（说明 Silhouette 没生效）')
    elif apply_fix:
        kids = kids_of(target)
        lines.append('')
        lines.append('子窗口 %d 个' % len(kids))
        for i, k in enumerate(kids):
            c2 = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(k, c2, 256)
            dup = g32.CreateRectRgn(0, 0, 1, 1)
            g32.CombineRgn(dup, parent_rgn, parent_rgn, RGN_COPY)
            old = g32.CreateRectRgn(0, 0, 1, 1)
            had = u32.GetWindowRgn(k, old)
            g32.DeleteObject(old)
            res = u32.SetWindowRgn(k, dup, True)
            lines.append('  kid#%d %-50s 原有region=%s SetWindowRgn=%s'
                         % (i, c2.value, RGN_NAMES.get(had, had), res))
        measure('已给子窗口设 region')
        lines.append('')
        lines.append('  注：这一步是**临时实验**，脚本不负责还原；')
        lines.append('      如果方块消失了，就把这个做法写进 common.py 固化下来。')
    else:
        lines.append('  （未加 --apply，只测量，没有改动任何窗口）')

    text = '\n'.join(lines)
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
