# -*- coding: utf-8 -*-
"""**不用重启桌宠**，从外部检查正在运行的那个窗口的真实状态。

为什么这是最强证据：
  之前所有"region 到底有没有设上"的判断，都是在本自动化会话里起一个假窗口
  测的 —— 而本会话里 WebView2 根本不渲染，所以那套测试**天然覆盖不到用户
  真实环境**。而用户的桌宠此刻正开着，那个窗口背后是真实渲染的 WebView2。
  GetWindowRgn 读的是系统里的窗口形状，不依赖任何渲染路径，所以对着活窗口
  读一次就能一锤定音。

判据：
  返回 0 (ERROR/无 region)  -> 窗口**根本没有形状**，SetWindowRgn 从未成功
  返回 3 (COMPLEXREGION)   -> 设上了。再看 bbox 是否等于整个客户区 ——
                              等于就是"白做"（形状覆盖整窗 = 没裁）
用法：probe_live_window.py [pid]
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'live_window.txt')

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32

# ---- 64 位下句柄必须声明 restype，否则被截成 int ----
u32.GetWindowDC.restype = ctypes.c_void_p
u32.GetWindowDC.argtypes = [ctypes.c_void_p]
u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.GetWindowLongW.restype = ctypes.c_long
u32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
u32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
u32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.EnumChildWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.CreateCompatibleDC.restype = ctypes.c_void_p
g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
g32.CreateDIBSection.restype = ctypes.c_void_p
g32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_uint, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_uint]
g32.CreateRectRgn.restype = ctypes.c_void_p
g32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int]
# GetWindowRgn 在 **user32**（和 SetWindowRgn 同一边）；GetRgnBox 才在 gdi32。
# 这两个库放错了会直接 AttributeError: function not found —— 上一轮踩过一次。
u32.GetWindowRgn.restype = ctypes.c_int
u32.GetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.GetRgnBox.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.SelectObject.restype = ctypes.c_void_p
g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.DeleteObject.argtypes = [ctypes.c_void_p]
g32.DeleteDC.argtypes = [ctypes.c_void_p]

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
PW_RENDERFULLCONTENT = 0x02
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
    return (struct.pack('>I', len(data)) + tag + data
            + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))


def png_write(path, w, h, bgra):
    """BGRA 底朝上 -> 写 PNG(top-down RGBA)。手写，不依赖 Pillow。"""
    raw = bytearray()
    for y in range(h - 1, -1, -1):
        raw.append(0)
        row = bgra[y * w * 4:(y + 1) * w * 4]
        for x in range(w):
            b, g, r, a = row[x * 4:x * 4 + 4]
            raw += bytes((r, g, b, a if a else 255))
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    with open(path, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n')
        fh.write(_chunk(b'IHDR', ihdr))
        fh.write(_chunk(b'IDAT', zlib.compress(bytes(raw), 6)))
        fh.write(_chunk(b'IEND', b''))


def grab(hwnd, path):
    """PrintWindow(PW_RENDERFULLCONTENT) 抓窗口自身 —— 能抓到 DirectComposition。"""
    rc = RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if not (0 < w <= 4000 and 0 < h <= 4000):
        return 'size out of range %dx%d' % (w, h)
    hdc = u32.GetWindowDC(hwnd)
    mem = g32.CreateCompatibleDC(hdc)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbm = g32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits),
                               None, 0)
    if not hbm:
        g32.DeleteDC(mem); u32.ReleaseDC(hwnd, hdc)
        return 'CreateDIBSection failed'
    g32.SelectObject(mem, hbm)
    ok = u32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    buf = ctypes.string_at(bits, w * h * 4)
    png_write(path, w, h, buf)
    from collections import Counter
    cnt = Counter()
    for i in range(0, len(buf), 4):
        cnt[(buf[i + 2], buf[i + 1], buf[i], buf[i + 3])] += 1
    g32.DeleteObject(hbm); g32.DeleteDC(mem); u32.ReleaseDC(hwnd, hdc)
    top = ', '.join('#%02X%02X%02X/A%d=%d' % (c[0], c[1], c[2], c[3], n)
                    for c, n in cnt.most_common(4))
    return 'printwindow=%s %dx%d  top: %s' % (ok, w, h, top)


def inspect(hwnd, lines, tag):
    cls = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, cls, 256)
    wr, cr = RECT(), RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(wr))
    u32.GetClientRect(hwnd, ctypes.byref(cr))
    ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    cw, ch = cr.right - cr.left, cr.bottom - cr.top
    lines.append('%-7s hwnd=%#010x visible=%s class=%s'
                 % (tag, hwnd, bool(u32.IsWindowVisible(hwnd)), cls.value))
    lines.append('        window=(%d,%d)-(%d,%d) %dx%d'
                 % (wr.left, wr.top, wr.right, wr.bottom,
                    wr.right - wr.left, wr.bottom - wr.top))
    lines.append('        client=%dx%d  exstyle=%#010x  layered=%s'
                 % (cw, ch, ex, bool(ex & WS_EX_LAYERED)))
    hrgn = g32.CreateRectRgn(0, 0, 1, 1)
    rtype = u32.GetWindowRgn(hwnd, hrgn)
    box = RECT()
    if rtype:
        g32.GetRgnBox(hrgn, ctypes.byref(box))
        bw, bh = box.right - box.left, box.bottom - box.top
        lines.append('        region : %s  bbox=(%d,%d)-(%d,%d) %dx%d'
                     % (RGN_NAMES.get(rtype, rtype), box.left, box.top,
                        box.right, box.bottom, bw, bh))
        if cw > 0 and ch > 0 and bw * bh >= cw * ch * 0.98:
            lines.append('        >>> 形状 bbox 几乎等于整个客户区 '
                         '== region 覆盖整窗（等于没裁）')
        else:
            lines.append('        >>> 形状确实小于客户区（真裁了）')
    else:
        lines.append('        region : ERROR/NO-REGION')
        lines.append('        >>> 窗口**根本没有 region** '
                     '== SetWindowRgn 从未成功过')
    g32.DeleteObject(hrgn)
    return cls.value


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 44164
    lines = ['=' * 74,
             '检查**正在运行**的桌宠窗口（pid=%d）—— 不重启、不打扰' % pid,
             '=' * 74]

    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, lparam):
        wpid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid:
            found.append(hwnd)
        return True

    u32.EnumWindows(cb, 0)
    lines.append('pid %d 顶层窗口数 = %d' % (pid, len(found)))
    lines.append('')

    if not found:
        lines.append('没找到该进程的顶层窗口（进程已退出？pid 不对？）')
        open(OUT, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
        print('no window')
        return

    target = None
    for i, hwnd in enumerate(found):
        cls = inspect(hwnd, lines, 'top#%d' % i)
        if target is None or 'WindowsForms' in cls:
            target = hwnd
        kids = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def ccb(child, lparam):
            kids.append(child)
            return True

        u32.EnumChildWindows(hwnd, ccb, 0)
        for j, k in enumerate(kids[:6]):
            c2 = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(k, c2, 256)
            r2 = RECT()
            u32.GetWindowRect(k, ctypes.byref(r2))
            lines.append('        kid#%d hwnd=%#010x class=%s %dx%d'
                         % (j, k, c2.value, r2.right - r2.left,
                            r2.bottom - r2.top))
        lines.append('')

    if target:
        lines.append('--- PrintWindow 抓取活窗口的真实渲染内容 ---')
        try:
            lines.append('  top : %s' % grab(target, os.path.join(HERE, 'live_top.png')))
        except Exception as exc:
            lines.append('  top grab FAILED %r' % (exc,))

        # 逐个抓子窗口 —— 关键问题：那块铺满整窗的底色到底是哪一层画的？
        # 父窗口的 region 已经证实生效（GetWindowRgn 读回 138x220），
        # 可屏幕上底色仍铺满 284x441，说明铺底的那一层没被形状裁到。
        lines.append('')
        lines.append('--- 逐个抓子窗口（找"谁在铺底"）---')
        kids = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def kcb(child, lparam):
            kids.append(child)
            return True

        u32.EnumChildWindows(target, kcb, 0)
        for j, k in enumerate(kids[:8]):
            c2 = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(k, c2, 256)
            kex = u32.GetWindowLongW(k, GWL_EXSTYLE)
            khrgn = g32.CreateRectRgn(0, 0, 1, 1)
            ktype = u32.GetWindowRgn(k, khrgn)
            g32.DeleteObject(khrgn)
            path = os.path.join(HERE, 'live_kid%d.png' % j)
            try:
                summary = grab(k, path)
            except Exception as exc:
                summary = 'FAILED %r' % (exc,)
            lines.append('  kid#%d %s  region=%s' % (j, c2.value,
                                                    RGN_NAMES.get(ktype, ktype)))
            lines.append('       %s' % summary)
            lines.append('       png=%s' % os.path.basename(path))

    text = '\n'.join(lines)
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(text + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
