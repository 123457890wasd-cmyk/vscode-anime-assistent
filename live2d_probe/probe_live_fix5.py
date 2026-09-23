# -*- coding: utf-8 -*-
"""第五轮 live 验证：宿主 Form 的浅灰底 = 「块状白」，以及挖洞能不能真挖掉。

背景（第五轮取证，见 README）：
  用户截图里那三块"白"的尺寸，用 DOM 盒 + region 的 pad 能逐个对上：
      气泡   DOM 盒 212x36.6 + pad5 -> 222x46.6   截图实测 222x46  ✓
       Airi  DOM 盒  33x15.6 + pad3 ->  39x21.6   截图实测  40x20  ✓
       online DOM 盒 37x14   + pad3 ->  43x20     截图实测  44x20  ✓
  也就是说：白块 = **窗口 region 的 pad 环**，而环里露出来的是宿主 Form 的底色
  （pywebview 走 transparent 分支时忘了给 Form.BackColor 赋值，保持 WinForms
  默认的 SystemColors.Control = #F0F0F0）。

怎么验证："环里那些浅灰像素"是一个可以直接数的数：
  AIRI_WIN_BG=off  → 复现（浅灰像素几百~上千）
  AIRI_WIN_BG=key  → 底色=键色#010203 + TransparencyKey，环里应该被挖成洞
                     ⇒ 浅灰像素≈0，且像素颜色≈桌面
以及一次"外部打洞"对照：不靠应用，直接在 hwnd 上 SetLayeredWindowAttributes
(LWA_COLORKEY, 实测底色)，看画面是不是同一个变化 —— 用来排除"改 CSS 才有效"
这类误判。

用法：probe_live_fix5.py        （自己起桌宠，自己收尾）
产物：live_fix5.txt + live5_off.png + live5_key.png + live5_off_desk.png
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PET = os.path.join(REPO, 'desktop_pet')
sys.path.insert(0, HERE)
from probe_live_fix3 import (shot, png_write, top_colors, pct_equal,  # noqa
                             RECT, u32, g32, SW_HIDE, SW_SHOW,
                             GWL_EXSTYLE, WS_EX_LAYERED, LWA_COLORKEY)

PY = r'C:\Users\Mr.hancard\.workbuddy\binaries\python\envs\live2d_probe\Scripts\python.exe'
OUT = os.path.join(HERE, 'live_fix5.txt')

u32.SetWindowLongPtrW = u32.SetWindowLongW          # 64 位下 W 版够用（值很小）


u32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
u32.GetWindowTextW.restype = ctypes.c_int


def find_window(pid, minw=120, minh=120):
    """找桌宠窗口：标题 == 'Airi' 或 pid 命中，取第一个够大的。

    第一版只按 pid 过滤，结果窗口明明起来了（日志里有 hwnd）却一个都没命中；
    所以这里**两种判据都试**，并且把"这一轮看到了哪些顶层窗口"原样记下来 ——
    否则下次还只会看到一句没信息量的 FAIL。
    返回 (hits, seen)
    """
    hits, seen = [], []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, lp):
        wpid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        tb = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, tb, 256)
        cb_ = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cb_, 256)
        r = RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        seen.append('0x%X pid=%d %dx%d title=%r cls=%s'
                    % (hwnd, wpid.value, w, h, tb.value, cb_.value))
        if (tb.value == 'Airi' or wpid.value == pid) and w >= minw and h >= minh:
            hits.append((hwnd, r))
        return True

    u32.EnumWindows(cb, 0)
    return hits, seen


def light_stats(buf):
    """数"浅灰底"像素：三通道都 >=225 且差 <=14（Form 默认底 #F0F0F0 那一档）。"""
    tot = len(buf) // 4
    light = 0
    verylight = 0
    for i in range(0, len(buf), 4):
        b, g, r = buf[i], buf[i + 1], buf[i + 2]
        if min(b, g, r) >= 225 and max(b, g, r) - min(b, g, r) <= 14:
            light += 1
        if (b + g + r) / 3.0 >= 200:
            verylight += 1
    return light, verylight, tot


def run_once(mode, L):
    env = dict(os.environ)
    env['AIRI_WIN_BG'] = mode
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    logf = os.path.join(HERE, '_live5_%s.log' % mode)
    fh = open(logf, 'w', encoding='utf-8', errors='replace')
    proc = subprocess.Popen([PY, 'standalone.py'], cwd=PET, env=env,
                            stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    L.append('--- AIRI_WIN_BG=%s  pid=%d ---' % (mode, proc.pid))
    target = None
    seen = []
    deadline = time.time() + 60
    while time.time() < deadline:
        hits, seen = find_window(proc.pid)
        if hits:
            target = hits[0]
            break
        if proc.poll() is not None:
            L.append('  进程提前退出 rc=%s' % proc.returncode)
            break
        time.sleep(0.4)
    if not target:
        L.append('  FAIL: 没等到窗口（顶层窗口共 %d 个，列前 25）' % len(seen))
        for s in seen[:25]:
            L.append('    ' + s)
        fh.close()
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        time.sleep(1.5)
        return None
    hwnd, r = target
    w, h = r.right - r.left, r.bottom - r.top
    x0, y0 = r.left, r.top
    L.append('  hwnd=0x%08X %dx%d at (%d,%d)' % (hwnd, w, h, x0, y0))
    # 等 Live2D 就绪 + region 上报稳定
    time.sleep(24)

    base = shot(x0, y0, w, h)
    png_write(os.path.join(HERE, 'live5_%s.png' % mode), w, h, base)
    light, very, tot = light_stats(base)
    L.append('  截图: 浅灰底像素 %d (%.2f%% of %d)  亮像素(均值>=200) %d (%.2f%%)'
             % (light, light * 100.0 / tot, tot, very, very * 100.0 / tot))
    L.append('  top: %s' % '  '.join('%s %.2f%%' % c for c in top_colors(base)))
    try:
        with open(logf, encoding='utf-8', errors='replace') as f2:
            for ln in f2:
                if 'winbg' in ln or 'airi-dwm' in ln:
                    L.append('  log: ' + ln.strip())
    except Exception:
        pass

    # 桌面参考：把窗口藏起来截同一块
    u32.ShowWindow(hwnd, SW_HIDE)
    time.sleep(0.5)
    desk = shot(x0, y0, w, h)
    if mode == 'off':
        png_write(os.path.join(HERE, 'live5_off_desk.png'), w, h, desk)
    u32.ShowWindow(hwnd, SW_SHOW)
    time.sleep(0.5)
    L.append('  与桌面一致 = %.2f%%' % pct_equal(base, desk))

    # ---- 外部对照：直接在 hwnd 上按实测底色打洞 ----
    if mode == 'off':
        ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
        # 实测底色 = 截图里出现最多的那个浅灰
        key = None
        for name, pct in top_colors(base, 8):
            rr, gg, bb = (int(name[1:3], 16), int(name[3:5], 16), int(name[5:7], 16))
            if min(rr, gg, bb) >= 200:
                key = (rr, gg, bb)
                break
        L.append('  外部 LWA_COLORKEY 对照：取键色 %s（实测底色）'
                 % ('#%02X%02X%02X' % key if key else 'none'))
        if key:
            colref = key[0] | (key[1] << 8) | (key[2] << 16)   # COLORREF = BGR
            ok = u32.SetLayeredWindowAttributes(hwnd, colref, 0, LWA_COLORKEY)
            time.sleep(1.2)
            after = shot(x0, y0, w, h)
            png_write(os.path.join(HERE, 'live5_off_colorkey.png'), w, h, after)
            l2, v2, _ = light_stats(after)
            L.append('  SetLayeredWindowAttributes -> %s  浅灰像素 %d -> %d'
                     % (ok, light, l2))
            L.append('  与桌面一致 %.2f%% -> %.2f%%'
                     % (pct_equal(base, desk), pct_equal(after, desk)))

    fh.close()
    try:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    time.sleep(2.0)
    return base


def main():
    L = ['=' * 74,
         '第五轮 live 验证：pad 环里的浅灰底 = 块状白；挖洞能不能真挖掉',
         '=' * 74,
         '判读： off 那次的"浅灰底像素"若为几百~上千 ⇒ 复现成功；',
         '       key 那次若≈0 且"与桌面一致"接近 100% ⇒ Form 底色已真被挖掉。',
         '']
    for mode in ('off', 'key'):
        try:
            run_once(mode, L)
        except Exception as e:
            L.append('  EXC: %r' % (e,))
        L.append('')
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
