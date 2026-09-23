"""probe_ui_preview.py — 把桌宠真页面渲染出来看（透明底全页截图 + 深色底合成）。

为什么要这个东西
----------------
1. **用户看的和探针里的不一样**：上一轮我在 headless Chrome 里怎么看都没有
   用户截图里那块白，但那不代表用户那边没有。所以我需要一个"能看整页、带 alpha、
   还能换成各种 CSS 变体"的渲染器 —— 它就是本脚本。
2. **改完得能自己先看一眼**：底板的形状/大小是我要跟用户确认的设计决策，
   让用户重启一次桌宠才发现不对，成本太高。

怎么拿到"整页 + alpha"
--------------------
`--default-background-color=00000000` 让 Chrome 用全透明底，`--screenshot` 出的
PNG 就带真 alpha 通道，再合成到深色底 = 还原用户桌面观感。

怎么让截图等到 Live2D 画出来
--------------------------
`--screenshot` 是在页面 load 事件之后拍的。页面里插一张 `/_slow.png`，
服务器收到请求先 sleep 一段时间再回 —— load 事件就被推到 Live2D 就绪之后。

用法
----
  python probe_ui_preview.py                 # 跑 VARIANTS 里全部变体
  python probe_ui_preview.py tight frame     # 只跑指定变体

产物
----
ui_preview.png              各变体纵向拼接（深色底）
ui_preview_<mode>.png       单变体整图
ui_preview.txt              每个变体的数值：视口、不透明像素、底板盒、角色外接框
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PET = REPO / 'desktop_pet'

LOG = HERE / 'ui_preview.txt'
SHEET = HERE / 'ui_preview.png'

sys.path.insert(0, str(PET))
import common as pet_common          # noqa: E402
import live2d_assets                 # noqa: E402

PORT = int(os.environ.get('PET_PREVIEW_PORT', '19903'))
SLOW_SECONDS = float(os.environ.get('PET_PREVIEW_WAIT', '13'))
VIEW = (284, 441)                    # 和用户机器上实测的 client 尺寸一致
OUTER = (1000, 800)                  # 外层窗口：实测视口 984x705，足够装下 iframe
BG = (32, 32, 32)                    # 深色桌面（用户当前是深色模式）

# ⚠️ 为什么用 iframe 装页面，而不是直接把 --window-size 设成 284x441
# ---------------------------------------------------------------
# 实测（_vp.txt）：headless=new 下 **视口宽度有 500px 下限**，
#   --window-size=284,441 -> innerWidth/innerHeight = 500x346
#   --window-size=500,346 -> 500x251
#   --window-size=1000,800 -> 984x705          （宽 -16、高 -95）
# 也就是说想直接拿到 284 宽的视口是**做不到**的（最小 500）。
# 上一版预览图右侧被裁掉、角色偏右，就是这个原因 —— 页面按 500 宽布局，
# 但截图只有 284 宽。
# 解法：外层给足尺寸（1000x800），用 width/height 写死的 iframe 把页面钉在
# 284x441 里（与用户 client 完全一致），截图之后再把 iframe 那块裁出来。

BROWSERS = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
]

DEFAULT_VARIANTS = ['off', 'tight', 'all', 'frame', 'square']

# 也做一张浅色底，因为用户切到浅色模式时桌面是浅的
LIGHT_BG = (238, 238, 242)

WRAPPER_TMPL = '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:transparent;overflow:hidden}
iframe{display:block;border:0;width:%dpx;height:%dpx;background:transparent}
</style></head><body>
<iframe id="f" src="/ui.html"></iframe>
<img src="/_slow.png" style="position:fixed;left:-9px;top:-9px;width:1px;height:1px" alt="">
<script>
(function () {
  var f = document.getElementById('f');
  function W() { return f.contentWindow; }
  function D() { return f.contentDocument; }

  function mode() {
    try { return W().CHAR_CARD || '?'; } catch (e) { return '?'; }
  }
  function box(el) {
    if (!el) return null;
    var r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top),
            Math.round(r.right), Math.round(r.bottom)];
  }
  function watchBBox() {
    try {
      var w = W();
      var l = w.__airi && w.__airi.live2d;
      if (!l || !l.ready || !l.app) return false;
      w.__charBox = box(l.app.canvas);
      return true;
    } catch (e) { return false; }
  }
  function addBubble() {
    try {
      var d = D();
      var a = d.getElementById('bubbleArea');
      if (!a) return;
      var el = d.createElement('div');
      el.className = 'bubble';
      el.textContent = '窗口透明搞定啦，点我说话。';
      a.appendChild(el);
    } catch (e) {}
  }
  function send(tag) {
    try {
      var d = D();
      var c = d.getElementById('charCard');
      var a = d.getElementById('bubbleArea');
      var b = a && a.querySelector('.bubble');
      var w = W();
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/preview-report', true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      xhr.send(JSON.stringify({
        tag: tag, card: mode(),
        vw: d.documentElement.clientWidth,
        vh: d.documentElement.clientHeight,
        live2d: !!(w.__airi && w.__airi.live2d && w.__airi.live2d.ready),
        cardBox: box(c),
        cardShown: !!(c && w.getComputedStyle(c).display !== 'none'),
        charBox: w.__charBox || null,
        bubbleBox: box(b)
      }));
    } catch (e) {}
  }

  var n = 0;
  var iv = setInterval(function () {
    n++;
    watchBBox();
    if (n === 8 || n === 20) addBubble();     // 隔几秒补一颗：气泡会被自动清理
    if (n > 40) clearInterval(iv);
  }, 400);
  setTimeout(function () { send('t=10s'); }, 10000);
  setTimeout(function () { send('t=12s'); }, 12000);
})();
</script>
</body></html>
'''


def make_handler(html_text):
    base = pet_common._make_handler(html_text)
    wrapper = (WRAPPER_TMPL % VIEW).encode('utf-8')

    class _H(base):
        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == '/_slow.png':
                time.sleep(SLOW_SECONDS)
                return self._plain(200, b'')
            if path == '/':
                # 外层壳：把真页面钉在 284x441 的 iframe 里（见上面 WRAPPER_TMPL 的说明）
                return self._blob(wrapper, 'text/html; charset=utf-8')
            return base.do_GET(self)

        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != '/preview-report':
                return self._plain(404, b'not found')
            ln = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(ln).decode('utf-8', 'replace')
            REPORTS.append(raw)
            return self._plain(200, b'ok')

    return _H


REPORTS = []


def run_one(browser, mode, log):
    html = pet_common.load_html(PORT, pet_common.find_character_image(),
                                live2d=True, silhouette=True, card=mode)

    srv = ThreadingHTTPServer(('127.0.0.1', PORT), make_handler(html))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    raw = HERE / ('_preview_raw_%s.png' % mode)
    if raw.exists():
        raw.unlink()
    profile = tempfile.mkdtemp(prefix='airi-preview-')
    proc = subprocess.Popen([
        browser,
        '--headless=new', '--disable-extensions', '--disable-background-networking',
        '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
        '--no-first-run', '--no-default-browser-check',
        '--hide-scrollbars', '--mute-audio',
        '--window-size=%d,%d' % OUTER,
        '--default-background-color=00000000',
        '--enable-unsafe-swiftshader',
        '--screenshot=%s' % raw,
        '--user-data-dir=%s' % profile,
        'http://127.0.0.1:%d/' % PORT,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok = False
    deadline = time.time() + SLOW_SECONDS + 60
    while time.time() < deadline:
        if raw.is_file() and raw.stat().st_size > 1000:
            s1 = raw.stat().st_size
            time.sleep(0.4)
            if raw.stat().st_size == s1:
                ok = True
                break
        time.sleep(0.3)
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    srv.shutdown()
    shutil.rmtree(profile, ignore_errors=True)

    log.append('=== card=%s ===' % mode)
    if not ok:
        log.append('  截图失败')
        return None
    im = Image.open(raw).convert('RGBA')
    W, H = im.size
    # 裁出 iframe 那一块（左上角 284x441）—— 外层只是给 iframe 撑出一块画布
    im = im.crop((0, 0, min(VIEW[0], W), min(VIEW[1], H)))
    W, H = im.size
    alpha = im.getchannel('A')
    opaque = sum(alpha.histogram()[200:])
    dark = Image.new('RGB', (W, H), BG)
    dark.paste(im, (0, 0), im)
    light = Image.new('RGB', (W, H), LIGHT_BG)
    light.paste(im, (0, 0), im)
    dark.save(HERE / ('ui_preview_%s.png' % mode))
    light.save(HERE / ('ui_preview_%s_light.png' % mode))

    log.append('  视图 %dx%d  不透明像素 %d (%.2f%%)'
               % (W, H, opaque, 100.0 * opaque / (W * H)))
    for r in list(REPORTS):
        try:
            d = json.loads(r)
        except Exception:
            continue
        log.append('  [%s] live2d=%s vw=%s vh=%s'
                   % (d.get('tag'), d.get('live2d'), d.get('vw'), d.get('vh')))
        log.append('        canvas 的 CSS 盒 = %s' % (d.get('charBox'),))
        log.append('        底板 shown=%s box=%s' % (d.get('cardShown'), d.get('cardBox')))
        log.append('        气泡 box=%s' % (d.get('bubbleBox'),))
    REPORTS[:] = []
    log.append('')
    return dark


def main():
    argv = [a for a in sys.argv[1:] if a in live2d_assets.CARD_MODES]
    modes = argv or DEFAULT_VARIANTS

    log = ['=' * 78,
           '桌宠真页面预览（透明底全页截图 → 深色底合成）',
           '=' * 78]
    ok, detail = live2d_assets.check_assets()
    log.extend('  ' + d for d in detail)
    if not ok:
        LOG.write_text('\n'.join(log) + '\n', encoding='utf-8')
        return 2
    browser = next((p for p in BROWSERS if Path(p).is_file()), None)
    if not browser:
        log.append('[FAIL] no Chrome/Edge')
        LOG.write_text('\n'.join(log) + '\n', encoding='utf-8')
        return 2
    log.append('  browser : %s' % browser)
    log.append('  视口    : %dx%d（= 用户机器实测 client）' % VIEW)
    log.append('  等待    : load 事件被 /_slow.png 拖住 %.0fs' % SLOW_SECONDS)
    log.append('')

    shots = []
    for m in modes:
        im = run_one(browser, m, log)
        if im is not None:
            shots.append((m, im))

    if shots:
        W = max(im.width for _, im in shots)
        H = sum(im.height + 6 for _, im in shots)
        sheet = Image.new('RGB', (W, H), (90, 90, 96))
        y = 0
        for _, im in shots:
            sheet.paste(im, (0, y))
            y += im.height + 6
        sheet.save(SHEET)
        log.append('拼接图: %s  %s' % (SHEET, sheet.size))
    LOG.write_text('\n'.join(log) + '\n', encoding='utf-8')
    print(LOG)
    return 0


if __name__ == '__main__':
    try:
        code = main()
    except Exception:
        import traceback
        LOG.write_text(traceback.format_exc(), encoding='utf-8')
        code = 3
    sys.exit(code)
