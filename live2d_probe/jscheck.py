"""_jscheck.py — 把 ui.html 里的 <script> 抠出来交给 node --check 做语法检查。

为什么需要：ui.html 的插值占位符（{{PORT}} 之类）在替换之前本身就是语法错误，
所以不能直接拿文件去 check；必须先过一遍 common.load_html()。
页面在 WebView2 里跑不起来时，最先要排除的就是"JS 有语法错"——
那种情况下整段脚本不执行，表现是"什么都不显示"，和别的故障长得很像。
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'desktop_pet'))
import common as pet_common  # noqa: E402

NODE = r'C:\Users\Mr.hancard\.workbuddy\binaries\node\versions\22.22.2-3\node.exe'

html = pet_common.load_html(1, None, live2d=True, silhouette=True, card='tight')
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
if not scripts:
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)

out = HERE / '_ui_js.js'
out.write_text('\n;\n'.join(scripts), encoding='utf-8')

# 模板占位符必须全部被替换掉，否则检查结果没有意义
left = re.findall(r'\{\{[A-Z_]+\}\}', html)
log = ['抠出 %d 段 <script>，共 %d 字符' % (len(scripts), len(out.read_text(encoding='utf-8')))]
log.append('未替换的占位符: %s' % (sorted(set(left)) or '无'))

r = subprocess.run([NODE, '--check', str(out)], capture_output=True, text=True)
log.append('node --check exit=%d' % r.returncode)
if r.stdout.strip():
    log.append('stdout: ' + r.stdout.strip()[:2000])
if r.stderr.strip():
    log.append('stderr: ' + r.stderr.strip()[:2000])
log.append('语法检查 ' + ('通过' if r.returncode == 0 else '失败'))

Path(HERE / '_jscheck.txt').write_text('\n'.join(log) + '\n', encoding='utf-8')
print('\n'.join(log))
