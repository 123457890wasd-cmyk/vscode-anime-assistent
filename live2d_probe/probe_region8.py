# -*- coding: utf-8 -*-
"""probe_region8: 不依赖 JS 桥，直接调 common._apply_window_region 验证 gdi32 修复。

建一个真 pywebview 窗口（transparent, 无 js_api），在后台线程里对它应用
v0.3.5 形状（圆角矩形 + 三角形多边形），读回 region 验证。
"""
import os
import sys
import threading
import time

PET = r"S:\My event\projects\vscode-anime-assistent\desktop_pet"
sys.path.insert(0, PET)

import common  # noqa: E402
import webview  # noqa: E402

OUT = os.path.join(PET, "..", "live2d_probe", "region8_test.txt")
OUT = os.path.normpath(OUT)
L = []
done = threading.Event()

HTML = '<html><body style="background:transparent;margin:0"></body></html>'

RECTS = [
    {'l': 60, 't': 30, 'r': 225, 'b': 90, 'rad': 14},          # 圆角矩形
    {'poly': [[142, 90], [156, 90], [149, 97]]},               # 三角形尾巴
    {'l': 110, 't': 400, 'r': 180, 'b': 430, 'rad': 10},       # 名牌板
    [20, 200, 260, 395],                                       # 旧式扁平行（轮廓矩形）
]
VW, VH = 285, 443


def work(window):
    try:
        # 等窗口就绪
        for _ in range(50):
            form = common._get_form(window)
            if form is not None and form.IsHandleCreated:
                break
            time.sleep(0.2)
        form = common._get_form(window)
        if form is None:
            L.append('FAIL: no form')
            return
        L.append('form OK handle=%s' % form.Handle)

        res = common._apply_window_region(window, RECTS, VW, VH)
        L.append('apply -> %r' % (res,))

        # 读回 region 验证
        import ctypes
        hwnd = common._get_hwnd(window)
        u32 = ctypes.windll.user32
        g32 = ctypes.windll.gdi32
        hrgn = g32.CreateRectRgn(0, 0, 0, 0)
        got = u32.GetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(hrgn))
        box = ctypes.wintypes.RECT() if hasattr(ctypes, 'wintypes') else None
        import ctypes.wintypes as wt
        box = wt.RECT()
        g32.GetRgnBox(ctypes.c_void_p(hrgn), ctypes.byref(box))
        L.append('GetWindowRgn type=%s bbox=(%d,%d,%d,%d)'
                 % (got, box.left, box.top, box.right, box.bottom))
        ok = bool(res.get('ok')) and got != 0  # NULLREGION=1 COMPLEXREGION=3 都算有形状
        L.append('VERDICT: %s' % ('PASS' if ok else 'FAIL'))
    except Exception as e:
        import traceback
        L.append('EXC %r' % (e,))
        L.append(traceback.format_exc())
    finally:
        done.set()
        try:
            window.destroy()
        except Exception:
            pass


L.append('pywebview %s' % getattr(webview, '__version__', '?'))
w = webview.create_window('region8test', html=HTML, width=285, height=443,
                          transparent=True, frameless=True, on_top=True)
try:
    webview.start(work, w)
except Exception as e:
    import traceback
    L.append('start EXC %r' % (e,))
    L.append(traceback.format_exc())

done.wait(timeout=30)
open(OUT, 'w', encoding='utf-8').write('\n'.join(L))
print('written', OUT)
