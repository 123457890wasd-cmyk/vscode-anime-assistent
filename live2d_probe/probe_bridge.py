# -*- coding: utf-8 -*-
"""对比「探针 venv」与「用户实际启动用的 Python」两边的 pywebview 内部结构。

为什么必须查这个：
  common.py:_get_hwnd() 依赖 `webview.platforms.winforms.BrowserView.instances`
  这个**内部实现细节**来取窗口 HWND。探针 venv 里是 pywebview 6.2.1，
  而用户是 Python312 里 pip 装的（requirements 只写 pywebview>=4.0）。
  两边版本不一致 -> instances 的挂载位置不同 -> _get_hwnd 返回 0
  -> set_window_region 返回 {'ok': False, 'why': 'no hwnd'}
  -> 前端 3 次失败后**永久放弃**，而且当前实现对此**不写任何日志**。

所以这个脚本要把两边都打出来对照。产物：bridge_probe.txt
"""
import json
import os
import re
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'bridge_probe_%s.txt' % ''.join(sys.version.split()[0].split('.')[:2]))

lines = []
lines.append('=' * 74)
lines.append('pywebview 内部结构对照')
lines.append('=' * 74)
lines.append('python     : %s' % sys.executable)
lines.append('version    : %s' % sys.version.split()[0])
lines.append('frozen/dpi : %s' % ('n/a',))

try:
    import webview
except Exception as exc:
    lines.append('import webview FAILED : %r' % (exc,))
    open(OUT, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    raise SystemExit(0)

lines.append('webview    : %s' % getattr(webview, '__file__', '?'))
ver = getattr(webview, '__version__', None)
if ver is None:
    try:
        from webview.util import get_version  # 有些版本放这里
        ver = get_version()
    except Exception:
        pass
lines.append('pywebview  : %s' % ver)

lines.append('')
lines.append('--- _get_hwnd 依赖的 BrowserView.instances ---')
try:
    from webview.platforms import winforms as wf
    lines.append('winforms module : %s' % wf.__file__)
    lines.append('has BrowserView : %s' % hasattr(wf, 'BrowserView'))
    bv = getattr(wf, 'BrowserView', None)
    if bv is not None:
        lines.append('BrowserView.instances exists : %s' % hasattr(bv, 'instances'))
        inst = getattr(bv, 'instances', None)
        if inst is not None:
            lines.append('type=%s len=%s' % (type(inst).__name__, len(inst)))
        lines.append('BrowserView 的其它 class 属性 : %s'
                     % [a for a in dir(bv) if a in ('instances', 'active', 'uid')])
    lines.append('winforms 里所有带 instances 的类 :')
    for name in dir(wf):
        obj = getattr(wf, name, None)
        if isinstance(obj, type) and hasattr(obj, 'instances'):
            lines.append('    %s  (instances id=%s)' % (name, id(getattr(obj, 'instances'))))
except Exception as exc:
    lines.append('inspect winforms FAILED : %r' % (exc,))

lines.append('')
lines.append('--- 直接扫 winforms 源码，找 instances 的赋值点 ---')
try:
    src = open(wf.__file__, encoding='utf-8').read()
    for m in re.finditer(r'^.*instances.*$', src, re.M):
        lines.append('    %s' % m.group(0).strip())
    lines.append('    源码总行数 = %d' % src.count('\n'))
except Exception as exc:
    lines.append('read source FAILED : %r' % (exc,))

lines.append('')
lines.append('--- Window 对象上能否直接拿到 hwnd? ---')
try:
    w = webview.Window
    cands = [a for a in dir(w) if 'handle' in a.lower() or 'hwnd' in a.lower()]
    lines.append('Window 里含 handle/hwnd 的属性 : %s' % cands)
except Exception as exc:
    lines.append('inspect Window FAILED : %r' % (exc,))

text = '\n'.join(lines)
with open(OUT, 'w', encoding='utf-8') as fh:
    fh.write(text + '\n')
print('written', OUT)
