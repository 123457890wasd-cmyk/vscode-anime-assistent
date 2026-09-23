"""probe_ui_blocks.py — 用真 Chrome 做 A/B，定位「谁画出了那块浅色方块」。

背景
----
用户截图里，角色下方的名牌（Airi / online）背后有一块 44x40 的实心浅色方块
（实测填充 rgb(241,228,233)，覆盖两行文字）。角色上方的对话框也有类似现象。

已经排除的
----------
- 不是模型美术：那块在窗口底部、宽 44、水平居中，是 DOM 布局位置（实测 bbox
  x120-163 / y386-425，正好盖住 .pet-name + .pet-status）
- 不是窗口底色：窗口本身已经真透明（Mica 已关），方块只占窗口 1.1%

做法
----
同一个 ui.html，真 Chrome headless，**只改一条 CSS**，跑 N 个变体，每个变体
用 `--default-background-color=00000000` 拿透明底全页截图，再合成到深色底 →
一眼就能看出哪个变体把方块干掉了。同时给每个变体算一个数：
**名牌矩形内"接近白"的像素占比** —— 方块在 = ~50%，方块没了 = 只剩文字（~5%）。

产物
----
ui_variants.png      所有变体纵向拼接，含变体名
ui_variant_<n>.png   每个变体的单独截图（合成到深色底）
ui_variants.txt      数值结果
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PET = REPO / 'desktop_pet'

LOG = HERE / 'ui_variants.txt'
SHEET = HERE / 'ui_variants.png'

sys.path.insert(0, str(PET))
import common as pet_common          # noqa: E402
import live2d_assets                 # noqa: E402

PORT = int(os.environ.get('PET_UI_BLOCKS_PORT', '19901'))

BROWSERS = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
]

# 名牌区域（窗口相对像素，来自对用户截图的实测）
PLATE = (112, 378, 176, 434)

# 每个变体：名字 + 追加的 CSS + 说明
VARIANTS = [
    ('0-baseline', '',
     '原样：预期名牌后面有方块'),
    ('1-no-name-filter', '.pet-name { filter: none !important; }',
     '去掉 .pet-name 的 filter: drop-shadow'),
    ('2-no-bubble-backdrop', '.bubble { backdrop-filter: none !important;'
                             ' -webkit-backdrop-filter: none !important; }',
     '去掉 .bubble 的 backdrop-filter'),
    ('3-no-gradclip', '.pet-name { background: none !important;'
                      ' -webkit-background-clip: border-box !important;'
                      ' background-clip: border-box !important;'
                      ' color: #ff6b9d !important; }',
     '名牌不再用 background-clip:text（改纯色字）'),
    ('4-all-off', '.pet-name { filter: none !important; }'
                  '.bubble { backdrop-filter: none !important;'
                  ' -webkit-backdrop-filter: none !important; }'
                  '.pet-name { background: linear-gradient(135deg,#ff6b9d 20%,#c084fc 80%) !important;'
                  ' -webkit-background-clip: text !important; background-clip: text !important;'
                  ' color: transparent !important; }',
     '1+2 同时关（看是不是要组合）'),
]

# 出现在页面里的一颗气泡（用真实 class，走真实 CSS）
BUBBLE_JS = '''
<script>
(function () {
  function add() {
    var area = document.getElementById('bubbleArea');
    if (!area) return;
    var el = document.createElement('div');
    el.className = 'bubble';
    el.id = 'probeBubble';
    el.textContent = '这是一个探测气泡，用来量它背后有没有方块。';
    area.appendChild(el);
    setTimeout(function () {
      var t = document.getElementById('probeTag');
      if (t) t.textContent = 'bubble=' + el.offsetWidth + 'x' + el.offsetHeight;
    }, 50);
  }
  setTimeout(add, 400);
  setTimeout(add, 4000);
})();
</script>
'''


def run_variant(browser, idx, name, css, note, log):
    html = pet_common.load_html(PORT, pet_common.find_character_image(), live2d=True)
    inject = ('<style id="probeVariant">%s</style>'
              '<div id="probeTag" style="position:absolute;left:0;top:0;'
              'font-size:9px;color:#fff;z-index:99"></div>%s' % (css, BUBBLE_JS))
    html = html.replace('</body>', inject + '</body>')

    srv = ThreadingHTTPServer(('127.0.0.1', PORT), pet_common._make_handler(html))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    raw = HERE / ('_ui_raw_%d.png' % idx)
    if raw.exists():
        raw.unlink()
    profile = tempfile.mkdtemp(prefix='airi-blocks-')
    proc = subprocess.Popen([
        browser,
        '--headless=new',
        '--disable-extensions',
        '--disable-background-networking',
        '--no-first-run', '--no-default-browser-check',
        '--hide-scrollbars', '--mute-audio',
        '--window-size=300,480',
        '--default-background-color=00000000',
        '--virtual-time-budget=20000',
        '--enable-unsafe-swiftshader',
        '--screenshot=%s' % raw,
        '--user-data-dir=%s' % profile,
        'http://127.0.0.1:%d/' % PORT,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok = False
    deadline = time.time() + 70
    while time.time() < deadline:
        if raw.is_file() and raw.stat().st_size > 1000:
            # 文件写完（体积连续两次一样）才算
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

    if not ok:
        log.append('  [%s] 截图失败（%s）' % (name, note))
        return None, None

    im = Image.open(raw).convert('RGBA')
    W, H = im.size
    dark = Image.new('RGB', (W, H), (32, 32, 32))
    dark.paste(im, (0, 0), im)

    # 名牌矩形内"接近白"的像素占比
    px = dark.load()
    x0, y0, x1, y1 = PLATE
    x1 = min(x1, W)
    y1 = min(y1, H)
    tot = n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = px[x, y]
            tot += 1
            if min(r, g, b) >= 180:
                n += 1
    frac = n / float(tot or 1)

    # 整页 alpha 覆盖率（确认页面真的是透明底）
    alpha = im.getchannel('A')
    hist = alpha.histogram()
    opaque = sum(hist[200:])
    log.append('  [%-20s] %s' % (name, note))
    log.append('      视图 %dx%d   不透明像素 %d (%.2f%%)   名牌矩形内近白占比 %.1f%%  气泡尺寸 %s'
               % (W, H, opaque, 100.0 * opaque / (W * H), 100.0 * frac,
                  '-'))
    snap = HERE / ('ui_variant_%d.png' % idx)
    dark.save(snap)
    return dark, frac


def main():
    log = []
    log.append('=' * 78)
    log.append('真 Chrome A/B：谁在名牌/气泡后面画浅色方块')
    log.append('=' * 78)
    ok, detail = live2d_assets.check_assets()
    log.extend('  ' + d for d in detail)
    if not ok:
        log.append('[FAIL] live2d assets not ready')
        LOG.write_text('\n'.join(log) + '\n', encoding='utf-8')
        return 2

    browser = next((p for p in BROWSERS if Path(p).is_file()), None)
    if not browser:
        log.append('[FAIL] no Chrome/Edge')
        LOG.write_text('\n'.join(log) + '\n', encoding='utf-8')
        return 2
    log.append('  browser: %s' % browser)
    log.append('  名牌探测矩形（窗口相对）: %s' % (PLATE,))
    log.append('')

    shots = []
    for i, (name, css, note) in enumerate(VARIANTS):
        im, frac = run_variant(browser, i, name, css, note, log)
        if im is not None:
            shots.append((name, im))
        log.append('')

    if shots:
        # 纵向拼接，左侧留白给变体名
        W = max(im.width for _, im in shots)
        H = sum(im.height + 4 for _, im in shots)
        sheet = Image.new('RGB', (W, H), (18, 18, 18))
        y = 0
        for _, im in shots:
            sheet.paste(im, (0, y))
            y += im.height + 4
        sheet.save(SHEET)
        log.append('拼接图: %s  %s' % (SHEET, sheet.size))
        log.append('各变体单独图: ui_variant_<n>.png')

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
