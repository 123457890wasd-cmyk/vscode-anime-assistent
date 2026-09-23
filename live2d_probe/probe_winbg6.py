# -*- coding: utf-8 -*-
"""第六轮：把 WinForms 的 TransparencyKey 到底设置了什么，单独量出来。

为什么必须单独量
----------------
第六轮 live 探针在真窗口上读到：

    AIRI_WIN_BG=key  ->  exstyle=0x000D0008 (WS_EX_LAYERED=True)
                         flags=LWA_ALPHA  keycolor=#010203  alpha=0/255

**flags 里没有 LWA_COLORKEY，而且 alpha=0** —— 这意味着整窗既完全透明、
又对鼠标完全穿透（分层窗口 alpha=0 = 看不见 + 点不到），
"点击没反应、也不能拖动"正好是这个症状。

但 live 环境有干扰（当时用户在全屏游戏里，前景窗口／DWM 合成都可能被改动），
所以不能直接把锅扣在 TransparencyKey 上。这里做一个**隔离实验**：
在一个干干净净的 WinForms Form 上逐步骤改属性，每步读回
GetLayeredWindowAttributes + exstyle，看是哪一步把状态变成 LWA_ALPHA/alpha=0 的。

产物：winbg_fix6.txt
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'winbg_fix6.txt')

clr = None
try:
    import clr as _clr                      # pythonnet
    clr = _clr
    clr.AddReference('System.Windows.Forms')
    clr.AddReference('System.Drawing')
except Exception as e:
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('pythonnet/clr 不可用: %r\n' % (e,))
    sys.exit(1)

import System.Windows.Forms as WinForms      # noqa: E402
import System.Drawing as Drawing             # noqa: E402

u32 = ctypes.windll.user32
u32.GetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p]
u32.GetLayeredWindowAttributes.restype = ctypes.c_int
u32.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, wt.DWORD,
                                           ctypes.c_ubyte, wt.DWORD]
u32.SetLayeredWindowAttributes.restype = ctypes.c_int
u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.GetWindowLongW.restype = ctypes.c_long

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x1
LWA_COLORKEY = 0x2
KEY = (1, 2, 3)

L = []
def note(s=''):
    L.append(str(s))

def state(hwnd, label):
    ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    key = wt.DWORD(0)
    flags = wt.DWORD(0)
    alpha = ctypes.c_ubyte(0)
    ok = u32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key),
                                        ctypes.byref(alpha), ctypes.byref(flags))
    if not ok:
        note('%-34s exstyle=0x%08X LAYERED=%-5s  (not layered / GetLWA failed)'
             % (label, ex & 0xFFFFFFFF, bool(ex & WS_EX_LAYERED)))
        return None
    names = []
    if flags.value & LWA_COLORKEY:
        names.append('LWA_COLORKEY')
    if flags.value & LWA_ALPHA:
        names.append('LWA_ALPHA')
    k = key.value
    note('%-34s exstyle=0x%08X LAYERED=%-5s flags=%-11s key=#%02X%02X%02X alpha=%d'
         % (label, ex & 0xFFFFFFFF, bool(ex & WS_EX_LAYERED),
            '|'.join(names) or 'none', k & 0xFF, (k >> 8) & 0xFF, (k >> 16) & 0xFF,
            alpha.value))
    return (flags.value, k, alpha.value)


def main():
    note('=' * 78)
    note('WinForms TransparencyKey 到底设置了什么（隔离实验，无 WebView2 / 无游戏）')
    note('=' * 78)

    form = WinForms.Form()
    form.FormBorderStyle = getattr(WinForms.FormBorderStyle, 'None')
    form.ShowInTaskbar = False
    hwnd = form.Handle.ToInt64()
    note('hwnd = 0x%X' % hwnd)
    note('')
    note('--- 干净 Form（只建了句柄） ---')
    s0 = state(hwnd, '0. baseline')

    note('')
    note('--- 只改 BackColor（不碰 TransparencyKey） ---')
    form.BackColor = Drawing.Color.FromArgb(*KEY)
    s1 = state(hwnd, '1. BackColor=#010203')

    note('')
    note('--- 再设 TransparencyKey = 同色（第五轮的写法） ---')
    form.TransparencyKey = Drawing.Color.FromArgb(*KEY)
    s2 = state(hwnd, '2. +TransparencyKey=#010203')

    note('')
    note('--- 补一刀 Opacity = 1.0（看能否把 alpha 修回 255） ---')
    form.Opacity = 1.0
    s3 = state(hwnd, '3. +Opacity=1.0')

    note('')
    note('--- 自己用 ctypes 直接打颜色键（对照组） ---')
    ok = u32.SetLayeredWindowAttributes(
        hwnd, KEY[0] | (KEY[1] << 8) | (KEY[2] << 16), 0, LWA_COLORKEY)
    note('SetLayeredWindowAttributes(LWA_COLORKEY, #010203) -> %s' % ok)
    s4 = state(hwnd, '4. 手动 LWA_COLORKEY')

    note('')
    note('--- 再读一次 Opacity 属性本身 ---')
    note('form.Opacity = %s' % form.Opacity)
    note('form.TransparencyKey = %s' % form.TransparencyKey)
    note('form.BackColor = %s' % form.BackColor)

    note('')
    note('=' * 78)
    note('判读')
    note('=' * 78)
    if s2 and (s2[0] & LWA_ALPHA) and not (s2[0] & LWA_COLORKEY):
        note('★ 第 2 步（设 TransparencyKey）之后 flags 里**只有 LWA_ALPHA**，')
        note('  且 alpha=%d —— 说明 WinForms 把整窗设成了"按 alpha 混合"，' % s2[2])
        note('  颜色键被覆盖。alpha==0 时窗口既看不见又鼠标穿透。')
        note('  → 这就是 key 模式点击/拖拽失效的机制。')
    elif s2 and (s2[0] & LWA_COLORKEY):
        note('第 2 步之后 LWA_COLORKEY 生效（flags=%s, key=#%02X%02X%02X, alpha=%d）'
             % (s2[0], s2[1] & 0xFF, (s2[1] >> 8) & 0xFF, (s2[1] >> 16) & 0xFF, s2[2]))
        note('  → 那 live 上看到的 LWA_ALPHA/alpha=0 是**别的代码/环境**改的，')
        note('    要往 pywebview 的透明 hack（Show/Hide、Opacity）方向查。')
    else:
        note('第 2 步没有让窗口变成分层窗口（s2=%r）' % (s2,))
    note('')
    note('对照：第 4 步手动打键之后 flags=%s（这一步是"正确状态"的样板）'
         % (s4[0] if s4 else None))

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('written', OUT)


if __name__ == '__main__':
    main()
