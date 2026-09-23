# -*- coding: utf-8 -*-
"""verify_silhouette_js.py — 用真 Chrome 验证「异形窗口 + 点击互动」的前端链路。

为什么必须用真浏览器
--------------------
这两块东西的核心都在 ui.html 里，而且依赖真实渲染出来的 WebGL 像素：

  * characterRects() 靠 gl.readPixels 读 canvas 的 alpha 通道，算出角色轮廓
  * alphaAt()        靠同一条路径判断"鼠标点到的到底是不是角色"
  * pushSilhouette() 把轮廓 + 气泡 + 名字的形状交给 Python 去 SetWindowRgn

自动化会话里 WebView2 不渲染（known issue），所以 pywebview 侧验证不了像素。
Chrome 会老老实实渲染，这里就能把前端这段逻辑跑实。

驱动方式沿用 verify_pet_ui.py：把 harness 注入 HTML 副本末尾，
页面跑完 POST 回来。桌宠本体 ui.html 不需要为测试做任何改动。

测什么
------
1. characterRects() 能否读出非空、在视口内、面积占比合理的轮廓
2. alphaAt() 在角色上 > 0、在空白处 == 0
3. collectUiRects() 能否抓到名字/状态
4. pushSilhouette() 是否真的把形状交给了 set_window_region
5. 点击角色 -> 命中 -> 分区 -> 取词 -> 出气泡 + 切表情（用假 pywebview.api 截获）
6. 拖拽**不**触发点击（两者共用 mousedown，不区分的话每次拖窗口都会说一句话）
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

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PET = REPO / 'desktop_pet'

LOG_PATH = HERE / 'silhouette_js_result.txt'
REPORT_PATH = HERE / 'silhouette_report.json'
DONE = HERE / '.silhouette_done'

# /event 通道收到的诊断（每个请求一个新 handler 实例，所以放模块级）
EVENTS = []

_log = []


def out(msg=''):
    _log.append(msg)
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))
    try:
        LOG_PATH.write_text('\n'.join(_log) + '\n', encoding='utf-8')
    except OSError:
        pass


sys.path.insert(0, str(PET))
import common as pet_common          # noqa: E402
import live2d_assets                 # noqa: E402

PORT = int(os.environ.get('SIL_JS_PORT', '19898'))

BROWSERS = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]

HARNESS = r'''
<script>
(function () {
  var t0 = Date.now();
  var report = {
    status: 'start', errors: [], core: false,
    sil: null, hit: null, ui: null, push: null, click: null, dragTest: null
  };

  // 假的 pywebview 桥：既截获调用，又给页面一个"宿主存在"的假象。
  // 必须在页面启动阶段就装好 —— startSilhouetteLoop 首帧在 900ms，
  // 晚于它装的话那次上报会被页面的存在性检查挡掉。
  window.__zones = [];
  window.__regionCalls = [];
  window.pywebview = { api: {
    pet_click: function (zone) {
      window.__zones.push(zone);
      return Promise.resolve({ text: 'HARNESS-REPLY-OK', emotion: 'happy' });
    },
    get_position: function () { return [0, 0]; },
    move_window: function () {},
    set_window_region: function (p) {
      window.__regionCalls.push({ rects: (p && p.rects || []).length,
                                  vw: p && p.vw, vh: p && p.vh });
      return Promise.resolve({ ok: true });
    }
  }};

  function ev(type, target, x, y) {
    target.dispatchEvent(new MouseEvent(type, {
      bubbles: true, cancelable: true, view: window,
      clientX: x, clientY: y, screenX: x, screenY: y,
      button: 0, buttons: 1
    }));
  }

  function done() {
    report.elapsedMs = Date.now() - t0;
    try {
      fetch('/harness-report', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(report)
      }).catch(function () {});
    } catch (e) {}
    var el = document.createElement('div');
    el.id = 'harness-done';
    document.body.appendChild(el);
  }

  var tries = 0;
  function waitReady() {
    tries++;
    if (window.live2d && window.live2d.ready) { report.core = true; return phaseSil(); }
    if (tries > 300) { report.errors.push('live2d 始终没 ready'); report.status = 'timeout'; return done(); }
    setTimeout(waitReady, 100);
  }

  var silTries = 0;

  function readSil() {
    var vpw = document.documentElement.clientWidth;
    var vph = document.documentElement.clientHeight;
    try {
      var chs = window.characterRects();
      var rects = chs.rects || [];
      var area = 0, oob = 0, minL = 1e9, maxR = -1e9, firstT = null, lastB = null;
      for (var i = 0; i < rects.length; i++) {
        var r = rects[i];
        area += (r[2] - r[0]) * (r[3] - r[1]);
        if (r[0] < -2 || r[1] < -2 || r[2] > vpw + 2 || r[3] > vph + 2) oob++;
        if (r[0] < minL) minL = r[0];
        if (r[2] > maxR) maxR = r[2];
        if (firstT === null) firstT = r[1];
        lastB = r[3];
      }
      report.sil = {
        count: rects.length, areaRatio: +(area / (vpw * vph)).toFixed(4),
        outOfBounds: oob, vw: vpw, vh: vph,
        source: chs.source, coverage: chs.coverage,
        minLeft: minL, maxRight: maxR, firstTop: firstT, lastBottom: lastB,
        sample: rects.slice(0, 3)
      };
    } catch (e) { report.errors.push('characterRects 抛错: ' + e); }
  }

  function phaseSil() {
    report.status = 'ready';
    readSil();
    // ⚠️ 为什么要在这里重试 —— 2026-09-23 实测踩到的一次假失败：
    //    live2d.ready 一翻真就立刻读 alpha，但那一帧**可能还没画进 framebuffer**，
    //    readPixels 于是读回全 0：轮廓 0 个、alphaAt(角色中心)=0、coverage=0。
    //    于是 26 条断言里 6 条 FAIL，看起来像"轮廓功能坏了"，其实只是读早了一步。
    //    当时是三个验收脚本连着跑（机器忙）触发的；单独重跑同代码立刻 26/26。
    //    这种假失败最坑 —— 它会让人去修一个不存在的 bug。所以：先等轮廓非空，
    //    3 秒（20×150ms）还读不出来，才让它带着空读数往下走（那时才是真坏）。
    if (report.sil && report.sil.count > 0) return afterSil();
    if (++silTries > 20) return afterSil();
    setTimeout(phaseSil, 150);
  }

  function afterSil() {
    try {
      var c = document.querySelector('#live2dStage canvas');
      var cr = c.getBoundingClientRect();
      report.hit = {
        center: window.alphaAt(cr.left + cr.width / 2, cr.top + cr.height * 0.5),
        corner: window.alphaAt(cr.left + 2, cr.top + 2),
        outside: window.alphaAt(1, 1)
      };
    } catch (e) { report.errors.push('alphaAt 抛错: ' + e); }

    try { report.ui = { count: window.collectUiRects().length }; }
    catch (e) { report.errors.push('collectUiRects 抛错: ' + e); }

    // 等几轮 pushSilhouette（首帧 900ms + 每 500ms 一次）
    report.silTries = silTries;
    setTimeout(phaseDegenerate, 2200);
  }

  // 造一份"全不透明"的 alpha —— 就是 WebView2 windowed hosting 下
  // WebGL 默认帧缓冲没有真 alpha 时的读数（readPixels 恒 255）。
  function opaqueGrid() {
    var c = document.querySelector('#live2dStage canvas');
    var w = c && c.width ? c.width : 8;
    var h = c && c.height ? c.height : 8;
    var buf = new Uint8Array(w * h * 4);
    for (var i = 0; i < buf.length; i++) buf[i] = 255;
    return { buf: buf, w: w, h: h };
  }

  // 这一段是上一版事故的回归测试：上一版在"alpha 读数不可信"时会产出
  // **整张画布**的轮廓，于是 region 等于整窗 -> 一点都没透明，而且全程不报错。
  function phaseDegenerate() {
    var orig = window.readAlphaGrid;
    try {
      // A) 只有 GL 那条路坏 -> 必须自动退到 2D 那条路，并且拿到可用轮廓
      window.readAlphaGrid = function (mode) {
        return mode === 'gl' ? opaqueGrid() : orig(mode);
      };
      var a = window.characterRects();
      report.degenA = {
        source: a.source,
        coverage: a.coverage === undefined ? null : +a.coverage.toFixed(4),
        rects: (a.rects || []).length
      };

      // B) 两条路都坏 -> **绝对不许**上报形状。
      //    推上去就等于把整窗设成 region（白做），只推气泡则会裁掉角色。
      var before = window.__regionCalls.length;
      window.readAlphaGrid = function () { return opaqueGrid(); };
      window.pushSilhouette(true);
      report.degenB = { callsBefore: before, callsAfter: window.__regionCalls.length };
    } catch (e) { report.errors.push('degenerate 阶段抛错: ' + e); }
    window.readAlphaGrid = orig;
    // 复原后重跑一次，把 silAlphaMode 从 'off' 拨回真实值，
    // 否则后面的 alphaAt（点击命中）会带着被污染的模式往下跑
    try { window.characterRects(); } catch (e) {}
    report.degenMode = window.silAlphaMode;
    setTimeout(phaseClick, 1200);
  }

  function canvasRect() {
    var c = document.querySelector('#live2dStage canvas');
    return c ? c.getBoundingClientRect() : null;
  }

  function phaseClick() {
    report.push = {
      calls: window.__regionCalls.length,
      lastRects: window.__regionCalls.length ? window.__regionCalls[window.__regionCalls.length - 1].rects : null
    };
    try {
      var cr = canvasRect();
      var pa = document.getElementById('petArea');
      var cx = cr.left + cr.width / 2, cy = cr.top + cr.height * 0.68;   // 身体段
      ev('mousedown', pa, cx, cy);
      ev('mousemove', document, cx, cy);      // 原地不动
      ev('mouseup', document, cx, cy);
      window.__clickPoint = [cx, cy];
    } catch (e) { report.errors.push('click 派发抛错: ' + e); }
    setTimeout(function () { phaseCollect(); }, 1400);
  }

  function phaseCollect() {
    try {
      var bs = document.querySelectorAll('#bubbleArea .bubble');
      var txt = bs.length ? bs[bs.length - 1].textContent : '';
      var cb = document.getElementById('charBox');
      report.click = {
        zones: window.__zones.slice(),
        bubbleCount: bs.length,
        bubbleText: txt,
        emotion: cb ? cb.getAttribute('data-emotion') : null
      };
    } catch (e) { report.errors.push('click 收集抛错: ' + e); }

    // 拖拽测试：按下 -> 移动 25px -> 松开。不该产生点击。
    var before = window.__zones.length;
    try {
      var cr = canvasRect();
      var pa = document.getElementById('petArea');
      var x = cr.left + cr.width / 2, y = cr.top + cr.height * 0.68;
      ev('mousedown', pa, x, y);
      ev('mousemove', document, x + 25, y + 8);
      ev('mouseup', document, x + 25, y + 8);
    } catch (e) { report.errors.push('drag 派发抛错: ' + e); }

    setTimeout(function () {
      report.dragTest = { zonesBefore: before, zonesAfter: window.__zones.length };
      report.status = 'done';
      done();
    }, 900);
  }

  setTimeout(waitReady, 600);
})();
</script>
'''


def make_handler(html_text: str):
    base = pet_common._make_handler(html_text)

    class _Harness(base):
        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if path == '/event':
                # ui.html 的 /event 通道（silhouetteDiag / desktopReady）。
                # 生产代码里这条通道会写进 .airi-pet.log —— 是排查
                # "为什么没透明"唯一的现场证据，所以这里也收下来做断言。
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length).decode('utf-8', 'replace')
                try:
                    EVENTS.append(json.loads(raw))
                except Exception:
                    EVENTS.append({'type': 'bad-json', 'raw': raw[:200]})
                return self._plain(200, b'{"status":"ok"}')
            if path != '/harness-report':
                return self._plain(404, b'not found')
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length).decode('utf-8', 'replace')
            try:
                payload = json.loads(raw)
            except Exception as exc:
                return self._plain(400, ('bad json: %r' % (exc,)).encode())
            REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding='utf-8')
            DONE.write_text('1', encoding='utf-8')
            return self._plain(200, b'ok')

    return _Harness


def main():
    out('=' * 74)
    out('异形窗口 + 点击互动 —— 前端链路验证（真 Chrome + 真 WebGL）')
    out('=' * 74)

    ok, detail = live2d_assets.check_assets()
    for ln in detail:
        out('  ' + ln)
    if not ok:
        out('[FAIL] Live2D 素材不可用')
        return 2
    out()

    char_img = pet_common.find_character_image()
    # 和 standalone.py 一致：silhouette=True
    html = pet_common.load_html(PORT, char_img, live2d=True, silhouette=True)
    out('  SILHOUETTE 注入 : %s'
        % ('true' if 'var SILHOUETTE = ("true" === "true");' in html else '??'))
    html = html.replace('</body>', HARNESS + '</body>')

    if DONE.exists():
        DONE.unlink()
    if REPORT_PATH.exists():
        REPORT_PATH.unlink()

    srv = ThreadingHTTPServer(('127.0.0.1', PORT), make_handler(html))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:%d/' % PORT
    out('  服务 : %s' % url)

    browser = next((p for p in BROWSERS if Path(p).is_file()), None)
    if browser is None:
        out('[FAIL] 找不到 Chrome/Edge')
        return 2
    out('  browser: %s' % browser)

    profile = tempfile.mkdtemp(prefix='airi-sil-')
    proc = subprocess.Popen([
        browser, '--headless=new', '--disable-extensions',
        '--disable-background-networking', '--disable-background-timer-throttling',
        '--disable-renderer-backgrounding', '--no-first-run',
        '--no-default-browser-check', '--hide-scrollbars', '--mute-audio',
        '--window-size=300,480', '--enable-unsafe-swiftshader',
        '--user-data-dir=%s' % profile, url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + 90
    while time.time() < deadline and not DONE.exists():
        time.sleep(0.3)
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    shutil.rmtree(profile, ignore_errors=True)

    if not REPORT_PATH.is_file():
        out()
        out('[FAIL] 没收到回报 —— 页面没跑完')
        return 2

    r = json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    sil = r.get('sil') or {}
    hit = r.get('hit') or {}
    ui = r.get('ui') or {}
    push = r.get('push') or {}
    click = r.get('click') or {}
    drag = r.get('dragTest') or {}

    out()
    out('=' * 74)
    out('原始数据')
    out('=' * 74)
    out('  status      : %s  (%s ms)' % (r.get('status'), r.get('elapsedMs')))
    out('  视口        : %sx%s' % (sil.get('vw'), sil.get('vh')))
    out('  轮廓矩形    : %s 个   面积占比 %s   越界 %s'
        % (sil.get('count'), sil.get('areaRatio'), sil.get('outOfBounds')))
    out('  轮廓纵向    : top=%s bottom=%s   横向 left=%s right=%s'
        % (sil.get('firstTop'), sil.get('lastBottom'),
           sil.get('minLeft'), sil.get('maxRight')))
    out('  前 3 个矩形 : %s' % json.dumps(sil.get('sample'), ensure_ascii=False))
    out('  alphaAt     : 角色中心=%s  角色左上角=%s  页面(1,1)=%s'
        % (hit.get('center'), hit.get('corner'), hit.get('outside')))
    out('  名字/状态矩形: %s 个' % ui.get('count'))
    out('  set_window_region 被调用: %s 次（最后一次带 %s 个矩形）'
        % (push.get('calls'), push.get('lastRects')))
    out('  点击结果    : zones=%s  气泡数=%s  气泡文本=%r  表情=%s'
        % (click.get('zones'), click.get('bubbleCount'),
           (click.get('bubbleText') or '')[:40], click.get('emotion')))
    out('  拖拽测试    : zones %s -> %s'
        % (drag.get('zonesBefore'), drag.get('zonesAfter')))
    da = r.get('degenA') or {}
    db = r.get('degenB') or {}
    out('  读数来源    : source=%s coverage=%s  （轮廓纵向跨度见上）'
        % (sil.get('source'), sil.get('coverage')))
    # 首次读 alpha 等了几次才拿到非空轮廓。>0 说明这次差点又踩到"读早一步"的假失败，
    # 但重试把它救回来了 —— 看到这个数非 0，就知道机器当时有多忙，不必再怀疑代码。
    out('  轮廓读重试  : %s 次（0=一次就读到，>0=重试救回）' % r.get('silTries'))
    out('  降级 A      : gl 坏 -> source=%s coverage=%s rects=%s   (期望 2d)'
        % (da.get('source'), da.get('coverage'), da.get('rects')))
    out('  降级 B      : 两条都坏 -> set_window_region %s -> %s 次   (期望不变)'
        % (db.get('callsBefore'), db.get('callsAfter')))
    diag_stages = [((e.get('payload') or {}).get('stage') or '')
                   for e in EVENTS if e.get('type') == 'silhouetteDiag']
    out('  /event 诊断 : 共 %d 条  stages=%s' % (len(EVENTS), diag_stages))
    out()

    checks = [
        ('页面跑完没报错', not r.get('errors'), r.get('errors')),
        ('Live2D 就绪', r.get('core'), ''),

        ('characterRects 读出非空轮廓', (sil.get('count') or 0) > 0,
         '%s 个' % sil.get('count')),
        ('轮廓矩形数量像样（>20）', (sil.get('count') or 0) > 20, '%s 个' % sil.get('count')),
        ('轮廓面积占比合理（3%~70%）',
         0.03 <= (sil.get('areaRatio') or 0) <= 0.70, sil.get('areaRatio')),
        ('轮廓全部在视口内（没越界）', sil.get('outOfBounds') == 0, sil.get('outOfBounds')),
        ('轮廓纵向跨度合理（底 > 顶）',
         (sil.get('firstTop') is not None and sil.get('lastBottom') is not None
          and sil.get('lastBottom') > sil.get('firstTop')),
         'top=%s bottom=%s' % (sil.get('firstTop'), sil.get('lastBottom'))),

        ('alphaAt 在角色中心读到像素（>0）', (hit.get('center') or 0) > 0, hit.get('center')),
        ('alphaAt 在 canvas 左上角读到 0（那里没角色）',
         hit.get('corner') == 0, hit.get('corner')),
        ('alphaAt 在页面(1,1) 读到 0', hit.get('outside') == 0, hit.get('outside')),

        ('collectUiRects 抓到名字/状态', (ui.get('count') or 0) >= 1, ui.get('count')),

        ('pushSilhouette 真的上报过形状', (push.get('calls') or 0) >= 1, push.get('calls')),
        ('上报的形状矩形数 >= 轮廓数（含气泡/名字）',
         (push.get('lastRects') or 0) >= (sil.get('count') or 1),
         'push=%s sil=%s' % (push.get('lastRects'), sil.get('count'))),

        ('点击角色命中并取到 zone', len(click.get('zones') or []) == 1,
         click.get('zones')),
        ('zone 判定为 body（0.68 高度处）', (click.get('zones') or [None])[0] == 'body',
         click.get('zones')),
        ('点击后弹出气泡', (click.get('bubbleCount') or 0) >= 1, click.get('bubbleCount')),
        ('气泡内容是宿主回的台词',
         'HARNESS-REPLY-OK' in (click.get('bubbleText') or ''), click.get('bubbleText')),
        ('点击后表情切到 happy', click.get('emotion') == 'happy', click.get('emotion')),

        ('拖拽不触发点击（zones 没增加）',
         (drag.get('zonesAfter') or 0) == (drag.get('zonesBefore') or 0),
         '%s -> %s' % (drag.get('zonesBefore'), drag.get('zonesAfter'))),

        # --- 上一版"没透明"事故的回归断言 ---
        # 上一版只要 readPixels 读出"整张画布都是角色"，就会把整窗设成 region
        # （等于什么都没裁），而且成功返回、零日志 —— 用户看到的就是一个方块。
        ('轮廓读数来源可信（gl 或 2d）',
         sil.get('source') in ('gl', '2d'), sil.get('source')),
        ('轮廓 coverage 落在合理区间（<0.70）',
         0 < (sil.get('coverage') or 0) < 0.70, sil.get('coverage')),
        ('★ gl 读数不可信时自动退到 2d 且轮廓可用',
         (da.get('source') == '2d' and (da.get('rects') or 0) > 20),
         'source=%s rects=%s' % (da.get('source'), da.get('rects'))),
        ('★ 两条读数都不可信时不设 region（绝不裁掉角色/白做）',
         (db.get('callsAfter') is not None
          and db.get('callsAfter') == db.get('callsBefore')),
         '%s -> %s' % (db.get('callsBefore'), db.get('callsAfter'))),
        ('轮廓不可用这件事被上报到宿主日志',
         'silhouette-unusable' in diag_stages, diag_stages),
        ('成功设上 region 时也留了诊断',
         'applied' in diag_stages, diag_stages),
        ('降级测试后读数模式已复原',
         r.get('degenMode') in ('gl', '2d'), r.get('degenMode')),
    ]

    out('=' * 74)
    out('断言')
    out('=' * 74)
    passed = 0
    for name, good, detailv in checks:
        out('  [%s] %-46s %s' % ('PASS' if good else 'FAIL', name,
                                 (':: %s' % (detailv,)) if detailv != '' else ''))
        if good:
            passed += 1

    out()
    out('结果 %d/%d 通过' % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


if __name__ == '__main__':
    sys.exit(main())
