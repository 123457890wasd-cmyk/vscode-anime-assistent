# -*- coding: utf-8 -*-
"""第六轮：把「点击 / 拖拽到底通不通」测成一个数，而不是靠肉眼猜。

为什么要有这个探针
------------------
第五轮为了挖掉「块状白」，给宿主 Form 设了 TransparencyKey（= 让窗口变成
WS_EX_LAYERED + LWA_COLORKEY）。用户随即反馈："点击没反应，也不能拖动"。
两种可能都得排掉，不能靠推理：

  A. 窗口层：分层 + 颜色键让鼠标消息不再落到角色上（窗口收不到输入）
  B. 页面层：事件到了，但被别的元素吃掉 / 命中判定不过

做法（全部是**可数的判据**）
--------------------------
1. 逐点 WindowFromPoint：某个点到底属于桌宠还是属于下层窗口
   -> 直接画出「哪些地方能点到」的形状；点得到 = 窗口层没问题
2. 真发一次左键点击（SetCursorPos + mouse_event），前后各截一张图，
   **数变化像素**：角色被点中会弹出一颗气泡（约 200x40），变化像素上千；
   没反应则只有 idle 动画的几十~几百像素噪声。
   -> 门槛取 800，远高于噪声、远低于一颗气泡
3. 真发一次拖拽（按下 -> 分 10 步移动 -> 松开），比对前后 GetWindowRect
   -> 位移非零 = 拖拽通
4. 读桌宠自己的 .airi-pet.log：pet_click / move_window / region 有没有留痕
   -> 区分「前端没命中」和「桥断了」

只在 WindowFromPoint 确认属于桌宠的点上点击 —— 避免误点到用户的别的窗口。

用法：probe_input6.py            （自己起桌宠、自己收尾）
产物：live_fix6.txt + live6_<mode>_before.png / _click.png / _drag.png
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
from probe_live_fix3 import (shot, png_write, RECT, u32, GWL_EXSTYLE,  # noqa
                             WS_EX_LAYERED)
from probe_live_fix5 import find_window  # noqa

PY = r'C:\Users\Mr.hancard\.workbuddy\binaries\python\envs\live2d_probe\Scripts\python.exe'
OUT = os.path.join(HERE, 'live_fix6.txt')
PET_LOG = os.path.join(PET, '.airi-pet.log')

LWA_ALPHA = 0x1
LWA_COLORKEY = 0x2
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
GA_ROOT = 2
CLICK_DIFF_MIN = 800          # 变化像素超过它才算"有反应"

u32.WindowFromPoint.restype = ctypes.c_void_p
u32.WindowFromPoint.argtypes = [wt.POINT]
u32.GetAncestor.restype = ctypes.c_void_p
u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
u32.GetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p]
u32.GetLayeredWindowAttributes.restype = ctypes.c_int
u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
                            ctypes.c_void_p]
u32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]

_PROC = None          # 当前桌宠进程，供异常路径收尾


def rect_of(hwnd):
    """hwnd 允许传 int 或 c_void_p —— 之前这里重复包了一层 c_void_p，
    直接 TypeError 把整个探针打断（而且异常路径没杀进程，留了两个孤儿桌宠）。"""
    if isinstance(hwnd, ctypes.c_void_p):
        hwnd = hwnd.value
    r = RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def layered_info(hwnd):
    """读回分层属性：flags / 键色 / alpha。这是判断 TransparencyKey 有没有生效的硬证据。"""
    key = wt.DWORD(0)
    flags = wt.DWORD(0)
    alpha = ctypes.c_ubyte(0)
    ok = u32.GetLayeredWindowAttributes(ctypes.c_void_p(hwnd), ctypes.byref(key),
                                        ctypes.byref(alpha), ctypes.byref(flags))
    if not ok:
        return 'GetLayeredWindowAttributes 失败（窗口不是分层窗口？）'
    names = []
    if flags.value & LWA_COLORKEY:
        names.append('LWA_COLORKEY')
    if flags.value & LWA_ALPHA:
        names.append('LWA_ALPHA')
    k = key.value
    return ('flags=%s(#%d) keycolor=#%02X%02X%02X alpha=%d/%d'
            % ('|'.join(names) or 'none', flags.value,
               k & 0xFF, (k >> 8) & 0xFF, (k >> 16) & 0xFF,
               alpha.value, 255))


def who_is_at(hwnd, x, y):
    """这个点属于谁？返回 ('pet'|'other'|'child-of-pet', 说明)"""
    pt = wt.POINT(int(x), int(y))
    h = u32.WindowFromPoint(pt)
    if not h:
        return 'other', 'none'
    root = u32.GetAncestor(h, GA_ROOT)
    if root == hwnd:
        return 'pet', 'root=pet hwnd=0x%X' % h
    tb = ctypes.create_unicode_buffer(128)
    u32.GetWindowTextW(u32.GetAncestor(h, GA_ROOT) or h, tb, 128)
    cls = ctypes.create_unicode_buffer(128)
    u32.GetClassNameW(h, cls, 128)
    return 'other', 'hwnd=0x%X cls=%s title=%r' % (h, cls.value, tb.value[:28])


def diff_pixels(a, b):
    n = min(len(a), len(b)) // 4
    d = 0
    for i in range(n):
        o = i * 4
        if (abs(a[o] - b[o]) > 12 or abs(a[o + 1] - b[o + 1]) > 12
                or abs(a[o + 2] - b[o + 2]) > 12):
            d += 1
    return d


def do_click(x, y):
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.07)
    u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def do_drag(x, y, dx, dy, steps=10):
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.20)
    u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.12)
    for k in range(1, steps + 1):
        u32.SetCursorPos(int(x + dx * k / steps), int(y + dy * k / steps))
        time.sleep(0.05)
    time.sleep(0.12)
    u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def log_tail(patterns, limit=14):
    out = []
    try:
        with open(PET_LOG, encoding='utf-8', errors='replace') as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        return ['(读不到 .airi-pet.log: %r)' % (e,)]
    for ln in lines:
        if any(p in ln for p in patterns):
            out.append(ln)
    if len(out) > limit:
        out = ['...（省略 %d 条）' % (len(out) - limit)] + out[-limit:]
    return out


def run_once(mode, L):
    global _PROC
    env = dict(os.environ)
    env['AIRI_WIN_BG'] = mode
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    logf = os.path.join(HERE, '_live6_%s.log' % mode)
    fh = open(logf, 'w', encoding='utf-8', errors='replace')
    # 完全模仿 VS Code 扩展的启动方式（src/extension.ts launchStandalonePet）：
    # detached + stdio ignore + windowsHide。第七轮发现：用 stdout 重定向的
    # spawn 起桌宠时，pywebview 的 JS 桥（pywebviewready）永远不注入 ——
    # 三种 AIRI_WIN_BG 模式全死；扩展方式起是否恢复，是判别"环境差异还是
    # 代码缺陷"的决定性实验。日志看 .airi-pet.log（不依赖 stdout）。
    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    proc = subprocess.Popen([PY, 'standalone.py'], cwd=PET, env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                            | CREATE_NO_WINDOW,
                            close_fds=True)
    _PROC = proc
    L.append('=' * 74)
    L.append('AIRI_WIN_BG=%s   pid=%d' % (mode, proc.pid))
    L.append('=' * 74)

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
        L.append('  FAIL: 没等到窗口（顶层 %d 个，列前 20）' % len(seen))
        for s in seen[:20]:
            L.append('    ' + s)
        fh.close()
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        return

    hwnd = target[0]
    x0, y0, w, h = rect_of(hwnd)
    L.append('  窗口 %dx%d at (%d,%d)  hwnd=0x%X' % (w, h, x0, y0, hwnd))

    # 等 Live2D 就绪 + region 上报稳定 + Form 底色处理
    time.sleep(26)

    r = RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    x0, y0, w, h = r.left, r.top, r.right - r.left, r.bottom - r.top
    L.append('  [窗口层]')
    ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    L.append('    exstyle=0x%08X  WS_EX_LAYERED=%s'
             % (ex & 0xFFFFFFFF, bool(ex & WS_EX_LAYERED)))
    L.append('    ' + layered_info(target[0]))

    # --- 逐点命中：哪些地方能点到桌宠 ---
    L.append('  [逐点 WindowFromPoint]  (fr = 窗口内相对百分比)')
    pts = [(0.50, 0.55), (0.50, 0.45), (0.50, 0.62), (0.42, 0.52), (0.58, 0.52),
           (0.50, 0.90), (0.50, 0.70), (0.06, 0.06)]
    pet_pts = []
    for fx, fy in pts:
        px, py = x0 + w * fx, y0 + h * fy
        kind, info = who_is_at(target[0], px, py)
        L.append('    (%.2f,%.2f) -> %s   %s' % (fx, fy, kind, info))
        if kind == 'pet':
            pet_pts.append((px, py, fx, fy))
    L.append('    其中属于桌宠的点: %d / %d' % (len(pet_pts), len(pts)))

    # --- 点击测试 ---
    L.append('  [点击测试] 真发左键，看角色有没有反应（变化像素 > %d 算有反应）'
             % CLICK_DIFF_MIN)
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    before = shot(x0, y0, w, h)
    png_write(os.path.join(HERE, 'live6_%s_before.png' % mode), w, h, before)
    reacted = None
    prev = before
    for px, py, fx, fy in pet_pts:
        do_click(px, py)
        time.sleep(1.1)
        now = shot(x0, y0, w, h)
        d = diff_pixels(prev, now)
        L.append('    点 (%.2f,%.2f) -> 变化像素 %d  %s'
                 % (fx, fy, d, '<= 有反应' if d > CLICK_DIFF_MIN else ''))
        if d > CLICK_DIFF_MIN:
            reacted = (fx, fy, d)
            png_write(os.path.join(HERE, 'live6_%s_click.png' % mode), w, h, now)
            break
        prev = now
    L.append('    结论: %s' % ('点击有反应 @ (%.2f,%.2f) 变化 %d 像素' % reacted
                              if reacted else '**点击完全没反应**'))

    # --- 拖拽测试 ---
    L.append('  [拖拽测试] 按住 -> 分 10 步移动 -> 松开，看窗口有没有跟着走')
    ax, ay = (pet_pts[0][0], pet_pts[0][1]) if pet_pts else (x0 + w / 2, y0 + h / 2)
    bx0, by0, _, _ = rect_of(hwnd)
    do_drag(ax, ay, 110, 70)
    time.sleep(0.9)
    ax0, ay0, _, _ = rect_of(hwnd)
    L.append('    窗口 (%d,%d) -> (%d,%d)   位移 = (%d,%d)  %s'
             % (bx0, by0, ax0, ay0, ax0 - bx0, ay0 - by0,
                '<= 拖拽通' if (ax0 != bx0 or ay0 != by0) else '**拖拽没反应**'))
    png_write(os.path.join(HERE, 'live6_%s_drag.png' % mode), w, h,
              shot(ax0, ay0, w, h))

    # --- 日志留痕 ---
    L.append('  [.airi-pet.log 留痕]')
    for ln in log_tail(['airi-winbg', 'airi-dwm', 'airi-input', 'airi-js',
                        'Desktop pet connected', 'airi-live2d] live2d']):
        L.append('    ' + ln)
    reg = log_tail(['airi-region'], limit=3)
    L.append('    --- region 最后 3 条 ---')
    for ln in reg:
        L.append('    ' + ln)

    fh.close()
    subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)


def main():
    L = ['=' * 74,
         '第六轮：点击互动 / 拖拽 到底通不通（真发鼠标事件，用数说话）',
         '=' * 74,
         '判读：',
         '  "逐点 WindowFromPoint" 里属于桌宠的点 = 能点到的地方。',
         '  点击 / 拖拽两行若出现「没反应」，再看 .airi-pet.log：',
         '    有 pet_click  -> 后端通了，问题在台词/气泡渲染',
         '    无 pet_click  -> 前端命中判定(alphaAt)没过 或 mousedown 没到',
         '    无 move_window-> 拖拽链路断在 JS/桥',
         '']
    for mode in ('dark',):
        try:
            run_once(mode, L)
        except Exception as e:
            import traceback
            L.append('  EXC: %r' % (e,))
            L.append(traceback.format_exc())
            # 异常路径也必须收尾 —— 上次漏了这一步，留了两个孤儿桌宠进程
            if _PROC is not None:
                try:
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(_PROC.pid)],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    time.sleep(1.5)
                except Exception:
                    pass
        L.append('')
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
