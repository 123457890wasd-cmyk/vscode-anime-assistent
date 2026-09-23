# -*- coding: utf-8 -*-
"""定位 pywebview 的安装位置与版本，并找出它处理 transparent 的代码行。"""
import io
import os
import sys

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'webview_loc_%d%d.txt' % sys.version_info[:2])

lines = []
lines.append('executable = %s' % sys.executable)
lines.append('version    = %s' % sys.version.split()[0])

try:
    import webview
    lines.append('webview.__file__ = %s' % webview.__file__)
    lines.append('webview.__version__ = %s'
                 % getattr(webview, '__version__', '?'))
    wf = os.path.join(os.path.dirname(webview.__file__),
                      'platforms', 'winforms.py')
    lines.append('winforms.py = %s  exists=%s' % (wf, os.path.exists(wf)))
    if os.path.exists(wf):
        with io.open(wf, 'r', encoding='utf-8', errors='replace') as fh:
            src = fh.readlines()
        lines.append('winforms.py 共 %d 行' % len(src))
        lines.append('--- 含 transparent / TransparencyKey / BackColor / Opacity'
                     ' / SetStyle 的行 ---')
        keys = ('transparent', 'TransparencyKey', 'BackColor', 'Opacity',
                'SetStyle', 'AllowsTransparency', 'WS_EX_LAYERED')
        for i, ln in enumerate(src, 1):
            low = ln.lower()
            if any(k.lower() in low for k in keys):
                lines.append('%5d: %s' % (i, ln.rstrip()))
        lines.append('--- 含 CreateWindow / WindowStyle / ExStyle 的行 ---')
        for i, ln in enumerate(src, 1):
            if any(k in ln for k in ('CreateParams', 'ExStyle', 'WindowStyle',
                                     'CreateWindow', 'Form()', 'Form(',
                                     'class BrowserView', 'def __init__')):
                lines.append('%5d: %s' % (i, ln.rstrip()))
except Exception as exc:
    import traceback
    lines.append('IMPORT FAILED: %r' % (exc,))
    lines.append(traceback.format_exc())

lines.append('')
lines.append('--- systrace: site-packages 候选 ---')
import site
for p in (site.getsitepackages() + [site.getusersitepackages()]):
    lines.append('  %s  exists=%s' % (p, os.path.exists(p)))

with io.open(out_path, 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(lines) + '\n')
print('written', out_path)
