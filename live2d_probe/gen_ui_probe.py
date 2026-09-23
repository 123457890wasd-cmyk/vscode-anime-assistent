# -*- coding: utf-8 -*-
"""生成气泡布局探针 HTML v2（自报状态版，不进 git）。

在页面里注入：window.onerror 捕获 + 延时 showBubble + 状态回写。
状态（气泡数量/矩形/错误）直接画进页面顶部，headless 截图即诊断。

用法（在仓库根的 live2d_probe/ 下执行）：
  python gen_ui_probe.py                # 生成 desktop_pet/_probe_{a,b}.html
  msedge --headless=new --window-size=286,441 --virtual-time-budget=4500 ^
         --screenshot=probe_a.png "file:///.../_probe_a.html"
⚠️ 探针必须先替换模板占位符（{{PORT}} 等），否则主 <script> 整块语法错误。
"""
import io, os

PROBE = os.path.dirname(os.path.abspath(__file__))          # live2d_probe/
PET = os.path.dirname(PROBE)                                # 仓库根 (workspace root)
SRC = os.path.join(PET, 'desktop_pet', 'ui.html')
OUT_DIR = os.path.join(PET, 'desktop_pet')

COMMON = """
<script>
window.__errs = [];
window.onerror = function(m, s, l) { window.__errs.push(m + ' @' + l); };
function __report(tag) {
  try {
    var area = document.getElementById('bubbleArea');
    var bs = area ? area.querySelectorAll('.bubble') : [];
    var lines = ['tag=' + tag, 'errs=' + window.__errs.join(' | '),
                 'areaTop=' + (area ? area.getBoundingClientRect().top.toFixed(1) : 'no-area'),
                 'n=' + bs.length];
    for (var i = 0; i < bs.length; i++) {
      var r = bs[i].getBoundingClientRect();
      lines.push('b' + i + '=' + Math.round(r.left) + ',' + Math.round(r.top) +
                 ' ' + Math.round(r.width) + 'x' + Math.round(r.height) +
                 ' txt=' + bs[i].textContent.slice(0, 8));
    }
    var d = document.createElement('pre');
    d.id = 'probeReport';
    d.style.cssText = 'position:fixed;top:2px;left:2px;z-index:9999;margin:0;' +
      'font:9px/1.3 monospace;color:#0f0;background:rgba(0,0,0,0.85);padding:4px;white-space:pre;';
    var old = document.getElementById('probeReport');
    if (old) old.remove();
    d.textContent = lines.join('\\n');
    document.body.appendChild(d);
  } catch (e) {}
}
</script>
"""

BOOT_A = """
<script>
window.addEventListener('load', function() {
  setTimeout(function() { showBubble('戳我一次，我就少看你一行代码。', false); }, 300);
  setTimeout(function() { showBubble('你这个 bug 还没改完呢，别戳我。', false); }, 1200);
  setTimeout(function() { __report('A-two-bubbles'); }, 2000);
});
</script>
"""

BOOT_B = """
<script>
window.addEventListener('load', function() {
  setTimeout(function() { showBubble('这是一条特别长的诊断信息，用来验证单条气泡在可用高度不足时会被压进窗口内部截断，而绝不会被窗口上沿横向切成两截，这里再继续补一点长度让文本至少折到第四行才行。', false); }, 300);
  setTimeout(function() { __report('B-long-single'); }, 2000);
});
</script>
"""

with io.open(SRC, 'r', encoding='utf-8') as f:
    html = f.read()

# 模板占位符按 load_html 的规则替换成合法值，否则主 <script> 整块语法错误
html = (html
        .replace('{{PORT}}', '1384')
        .replace('{{CHARACTER_IMAGE}}', 'assets/character.png')
        .replace('{{LIVE2D_ENABLED}}', 'true')
        .replace('{{SILHOUETTE}}', 'true')
        .replace('{{CHAR_CARD}}', 'square'))

assert '</body>' in html
for name, boot in (('_probe_a.html', COMMON + BOOT_A), ('_probe_b.html', COMMON + BOOT_B)):
    with io.open(os.path.join(OUT_DIR, name), 'w', encoding='utf-8') as f:
        f.write(html.replace('</body>', boot + '</body>'))
print('probes v2 written to', OUT_DIR)
