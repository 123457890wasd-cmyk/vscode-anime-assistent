# -*- coding: utf-8 -*-
"""ui.html 全部 <script> 块的语法检查（node --check），绕开 PowerShell 管道噪声。

用法：先跑 gen_ui_probe.py 生成 desktop_pet/_probe_a.html（占位符已替换），
再跑本脚本。原始 ui.html 的 {{PORT}} 等占位符不是合法 JS，直接抽出来
--check 必然整块报错 —— 必须从探针文件提取。

脚本块抽到 %TEMP% 检查，不污染仓库。
"""
import io
import os
import re
import subprocess
import tempfile

PROBE = os.path.dirname(os.path.abspath(__file__))            # live2d_probe/
PET_ROOT = os.path.dirname(PROBE)                             # 仓库根
PROBE_HTML = os.path.join(PET_ROOT, 'desktop_pet', '_probe_a.html')
NODE = r"S:\nodejs\node.exe"

with io.open(PROBE_HTML, 'r', encoding='utf-8') as f:
    html = f.read()

blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
results = []
tmp = tempfile.mkdtemp(prefix='airi_jscheck_')
for i, src in enumerate(blocks):
    if not src.strip():
        continue
    path = os.path.join(tmp, '_script_%d.js' % i)
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    r = subprocess.run([NODE, '--check', path], capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    err = r.stderr.decode('utf-8', 'replace').strip()
    results.append('script#%d exit=%d\n%s' % (i, r.returncode, err[:800]))

out = '\n----\n'.join(results) + '\n'
with io.open(os.path.join(PROBE, '_jscheck_out.txt'), 'w', encoding='utf-8') as f:
    f.write(out)
print(out)
