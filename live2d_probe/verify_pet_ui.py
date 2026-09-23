"""
verify_pet_ui.py — 端到端验证「桌宠自己的 ui.html + 自己的资源服务器」

为什么要有这个脚本
------------------
live2d_probe/probe.html 证明的是**探针页**能渲染；这里要证明的是
**桌宠本体**能渲染。两者不是一回事：本脚本用的就是

    desktop_pet/common.py:load_html() + start_asset_server()
    desktop_pet/ui.html（含 Live2D 模块、情绪映射、眨眼驱动）

也就是用户实际会跑到的那条路径。用的是真 Chrome + 真 WebGL（不是 mock）。

测什么
------
1. Live2D 是否接管了角色位（#live2dStage.visible、#charImg 隐藏）
2. canvas 上是否真的画出了东西（读 alpha 通道算覆盖率）
3. ticker 是否在跑（隔 600ms 两次像素读数是否不同）
4. **眨眼驱动是否正确**（min < 0.5 说明眨过；last > 0.9 说明眼睛重新睁开了
   —— 后者才是关键：接缝挂错时 ParamEyeLOpen 会被反复累乘，眼睛永远闭着）

驱动脚本是注入到 HTML 副本里的，桌宠本体 ui.html 不需要为了测试改任何东西。
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

LOG_PATH = HERE / 'pet_ui_result.txt'
SHOT_PATH = HERE / 'pet_ui_shot.png'
DONE = HERE / '.pet_ui_done'

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

PORT = int(os.environ.get('PET_UI_PORT', '19899'))

BROWSERS = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]

# 注入到 HTML 副本末尾的驱动器（桌宠本体不含这段）
HARNESS = r'''
<script>
(function () {
  var report = {
    stageVisible: false, charImgHidden: false, cssHidden: false,
    canvas: null, opaque: 0, ratio: 0, shot: null,
    anim: { changedPixels: null, sampled: false },
    blink: { samples: 0, min: 1, max: 0, last: 1, below01: 0 },
    core: false, errors: []
  };

  function stage() { return document.getElementById('live2dStage'); }
  function canvas() { return document.querySelector('#live2dStage canvas'); }

  function readRGBA() {
    var c = canvas();
    if (!c) return null;
    var c2 = document.createElement('canvas');
    c2.width = c.width; c2.height = c.height;
    var ctx = c2.getContext('2d');
    ctx.clearRect(0, 0, c2.width, c2.height);
    ctx.drawImage(c, 0, 0);
    return { d: ctx.getImageData(0, 0, c2.width, c2.height).data, w: c2.width, h: c2.height, c: c2 };
  }

  function eyes() {
    try {
      var m = window.__airi && window.__airi.live2d && window.__airi.live2d.model;
      if (!m) return null;
      var core = m.internalModel.coreModel;
      var ids = Array.from(core._model.parameters.ids);
      var v = core._model.parameters.values;
      return [v[ids.indexOf('ParamEyeLOpen')], v[ids.indexOf('ParamEyeROpen')]];
    } catch (e) { return null; }
  }

  function finish(status) {
    report.status = status;
    report.elapsedMs = Date.now() - t0;
    report.saveCalls = window.__saveCalls;
    report.postMin = window.__postMin;
    report.postMax = window.__postMax;
    report.blinkFrames = window.__blinkFrames;
    report.blinkCycles = window.__blinkCycles;
    report.beforeFirst = window.__beforeFirst;
    var st = stage();
    report.stageVisible = !!(st && st.classList.contains('visible'));
    report.charImgHidden = (document.getElementById('charImg').style.display === 'none');
    report.cssHidden = !document.getElementById('cssChar').classList.contains('visible');
    report.core = (typeof window.Live2DCubismCore !== 'undefined');
    var r = readRGBA();
    if (r) {
      report.canvas = r.w + 'x' + r.h;
      var n = 0;
      for (var i = 3; i < r.d.length; i += 4) if (r.d[i] > 8) n++;
      report.opaque = n;
      report.ratio = +(n / (r.w * r.h)).toFixed(4);
      // 合成到浅色底上，方便人眼直接看
      var vis = document.createElement('canvas');
      vis.width = r.w; vis.height = r.h;
      var vc = vis.getContext('2d');
      vc.fillStyle = '#eef1f6';
      vc.fillRect(0, 0, vis.w, vis.h);
      vc.drawImage(r.c, 0, 0);
      report.shot = vis.toDataURL('image/png');
    }
    try {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/harness-report', true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      xhr.send(JSON.stringify(report));
    } catch (e) { report.errors.push('post: ' + e); }
  }

  var t0 = Date.now();

  // ---- phase 1: 等 Live2D 就绪 ----
  var waited = 0;
  (function waitReady() {
    var v = stage() && stage().classList.contains('visible');
    var canvasThere = !!canvas();
    if (v || waited > 20000) { phase2(); return; }
    waited += 200;
    setTimeout(waitReady, 200);
  })();

  // ---- phase 2: 诊断 + rAF 采样眨眼 9 秒 ----
  function phase2() {
    // 先查清楚：core 句柄在不在、眨眼接缝到底有没有被调用
    try {
      var m = window.__airi && window.__airi.live2d && window.__airi.live2d.model;
      var intr = m && m.internalModel;
      var core = intr && intr.coreModel;
      var d = { hasModel: !!m, hasIntr: !!intr, hasCore: !!core };
      if (core) {
        d.coreCtor = (core.constructor && core.constructor.name) || '?';
        d.saveSrc = String(core.saveParameters).slice(0, 150);
        d.saveIsPatched = (String(core.saveParameters).indexOf('idxL') >= 0 ||
                           String(core.saveParameters).indexOf('blink') >= 0);
        try {
          var ids = Array.from(core._model.parameters.ids);
          d.paramCount = ids.length;
          d.eyeL = ids.indexOf('ParamEyeLOpen');
          d.eyeR = ids.indexOf('ParamEyeROpen');
        } catch (e) { d.idsErr = String(e); }
        // 包一层：记录「补丁跑完那一刻」ParamEyeLOpen 被改成什么了。
        //
        // ⚠️ 为什么必须在接缝处量、不能在 rAF 里量：
        // 帧序是 loadParameters() → 动作写入 → saveParameters() → 我们补丁 →
        // update() → 画。下一帧开头 loadParameters() 会用 saveParameters 时的
        // 快照把参数**恢复回基线**，所以帧外（rAF）读到的永远是恢复后的 1 ——
        // 第一版就是因此误判成"从没眨过眼"。作者自己的 drawn(id) 诊断也是
        // 在接缝处拷贝一份值，原因相同。
        try {
          var inner = core.saveParameters.bind(core);
          window.__saveCalls = 0;
          window.__postMin = 9;
          window.__postMax = -9;
          window.__blinkFrames = 0;
          window.__blinkCycles = 0;
          var closed = false;
          var at = d.eyeL;
          core.saveParameters = function () {
            window.__saveCalls++;
            var values = at >= 0 ? core._model.parameters.values : null;
            var before = values ? values[at] : null;
            var r = inner();
            var after = values ? values[at] : null;
            if (after !== null) {
              if (after < window.__postMin) window.__postMin = +after.toFixed(4);
              if (after > window.__postMax) window.__postMax = +after.toFixed(4);
              if (after < before) window.__blinkFrames++;
              // 一次完整眨眼 = 闭到 <0.5 之后又回到 >0.9
              if (after < 0.5) closed = true;
              else if (after > 0.9 && closed) { window.__blinkCycles++; closed = false; }
            }
            if (window.__beforeFirst === undefined) window.__beforeFirst = before;
            return r;
          };
        } catch (e) { d.wrapErr = String(e); }
      }
      report.diag = d;
    } catch (e) { report.diag = { err: String(e) }; }

    var t1 = performance.now();
    (function sample(now) {
      var e = eyes();
      if (e) {
        report.blink.samples++;
        var lo = Math.min(e[0], e[1]);
        if (lo < report.blink.min) report.blink.min = +lo.toFixed(3);
        if (lo > report.blink.max) report.blink.max = +lo.toFixed(3);
        if (lo < 0.1) report.blink.below01++;
        report.blink.last = +lo.toFixed(3);
      }
      if (now - t1 < 9000) { requestAnimationFrame(sample); return; }
      phase3();
    })(performance.now());
  }

  // ---- phase 3: 画面是否在动（证明 ticker 真的在跑）----
  function phase3() {
    var a = readRGBA();
    setTimeout(function () {
      var b = readRGBA();
      if (a && b) {
        var n = 0, len = Math.min(a.d.length, b.d.length);
        for (var i = 0; i < len; i += 4) {
          if (a.d[i] !== b.d[i] || a.d[i + 1] !== b.d[i + 1] ||
              a.d[i + 2] !== b.d[i + 2] || a.d[i + 3] !== b.d[i + 3]) n++;
        }
        report.anim.changedPixels = n;
        report.anim.sampled = true;
      }
      finish('done');
    }, 600);
  }
})();
</script>
'''


def make_handler(html_text: str):
    """复用桌宠生产环境的处理器，只额外加一个测试回报端点。"""
    base = pet_common._make_handler(html_text)

    class _Harness(base):
        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != '/harness-report':
                return self._plain(404, b'not found')
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length).decode('utf-8', 'replace')
            try:
                payload = json.loads(raw)
            except Exception as exc:
                return self._plain(400, ('bad json: %r' % (exc,)).encode())
            shot = payload.pop('shot', None)
            (HERE / 'pet_ui_report.json').write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            if isinstance(shot, str) and shot.startswith('data:image/png;base64,'):
                import base64
                (SHOT_PATH).write_bytes(base64.b64decode(shot.split(',', 1)[1]))
            DONE.write_text('1', encoding='utf-8')
            return self._plain(200, b'ok')

    return _Harness


def main():
    out('=' * 74)
    out('桌宠本体 ui.html 端到端验证（真 Chrome + 真 WebGL）')
    out('=' * 74)

    ok, detail = live2d_assets.check_assets()
    for ln in detail:
        out('  ' + ln)
    if not ok:
        out('[FAIL] live2d assets not ready')
        return 2
    out()

    char_img = pet_common.find_character_image()
    out(f'  立绘(char_img) : {char_img}')

    # 与 standalone.py 完全一致的调用序列
    html = pet_common.load_html(PORT, char_img, live2d=True)
    out(f'  LIVE2D_ENABLED 注入: {"true" if "var LIVE2D_ENABLED = true" in html else "??"}')
    html = html.replace('</body>', HARNESS + '</body>')

    if DONE.exists():
        DONE.unlink()
    for stale in (SHOT_PATH, HERE / 'pet_ui_report.json'):
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass

    srv = ThreadingHTTPServer(('127.0.0.1', PORT), make_handler(html))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{PORT}/'
    out(f'  服务(生产处理器) : {url}')
    out()

    browser = None
    for p in BROWSERS:
        if Path(p).is_file():
            browser = p
            break
    if browser is None:
        out('[FAIL] no Chrome/Edge found')
        return 2
    out(f'  browser: {browser}')

    profile = tempfile.mkdtemp(prefix='airi-harness-')
    proc = subprocess.Popen([
        browser,
        '--headless=new',
        '--disable-extensions',
        '--disable-background-networking',
        '--disable-background-timer-throttling',
        '--disable-renderer-backgrounding',
        '--no-first-run',
        '--no-default-browser-check',
        '--hide-scrollbars',
        '--mute-audio',
        '--window-size=300,480',
        '--enable-unsafe-swiftshader',
        f'--user-data-dir={profile}',
        url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + 75
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

    rp = HERE / 'pet_ui_report.json'
    if not rp.is_file():
        out()
        out('[FAIL] 没有收到回报 —— 页面没跑完')
        return 2

    r = json.loads(rp.read_text(encoding='utf-8'))
    out()
    out('=' * 74)
    out('结果')
    out('=' * 74)
    out(f"  status        : {r.get('status')}   ({r.get('elapsedMs')} ms)")
    out(f"  cubism core   : {r.get('core')}")
    out(f"  live2d stage  : visible={r.get('stageVisible')}")
    out(f"  charImg 隐藏  : {r.get('charImgHidden')}   cssChibi 隐藏: {r.get('cssHidden')}")
    out(f"  canvas        : {r.get('canvas')}  opaque={r.get('opaque')} ratio={r.get('ratio')}")
    a = r.get('anim') or {}
    out(f"  ticker 在跑   : changed={a.get('changedPixels')} px / 600ms")
    b = r.get('blink') or {}
    out(f"  [参考] rAF 帧外采样: n={b.get('samples')} min={b.get('min')} "
        f"max={b.get('max')} last={b.get('last')}")
    out(f"         ^ 帧外读到的恒为 1 是正常的：loadParameters() 已恢复基线，"
        f"不能用来判断眨没眨眼")
    d = r.get('diag') or {}
    out(f"  --- 诊断 ---")
    out(f"  core 句柄     : model={d.get('hasModel')} internalModel={d.get('hasIntr')} "
        f"coreModel={d.get('hasCore')} ctor={d.get('coreCtor')}")
    out(f"  参数          : count={d.get('paramCount')} eyeL={d.get('eyeL')} eyeR={d.get('eyeR')}")
    out(f"  saveParameters 被引擎调用次数: {r.get('saveCalls')}")
    out(f"  接缝处 eye 值 : postMin={r.get('postMin')} postMax={r.get('postMax')} "
        f"blinkFrames={r.get('blinkFrames')} completeCycles={r.get('blinkCycles')}")
    out(f"  眨眼补丁已装上: {d.get('saveIsPatched')}")
    out()

    checks = [
        ('Live2D 接管角色位', bool(r.get('stageVisible')), f"visible={r.get('stageVisible')}"),
        ('图片与 CSS 角色已隐藏', bool(r.get('charImgHidden')) and bool(r.get('cssHidden')),
         f"img={r.get('charImgHidden')} css={r.get('cssHidden')}"),
        ('canvas 真的画出了模型', (r.get('ratio') or 0) >= 0.05,
         f"opaque={r.get('opaque')} ({r.get('ratio')})"),
        ('ticker 在跑（画面在变）', (a.get('changedPixels') or 0) > 0,
         f"{a.get('changedPixels')} px"),
        # 眨眼必须在**接缝处**量（帧外读恒为基线 1，见上面注释）
        ('眨眼把眼闭上过（接缝值）', (r.get('postMin') if r.get('postMin') is not None else 9) < 0.2,
         f"postMin={r.get('postMin')}"),
        ('完成完整眨眼循环（闭->开）', (r.get('blinkCycles') or 0) >= 1,
         f"cycles={r.get('blinkCycles')} frames={r.get('blinkFrames')}"),
        ('眼睛没被卡在闭合（接缝没挂错）', (r.get('postMax') if r.get('postMax') is not None else 0) > 0.9,
         f"postMax={r.get('postMax')}"),
    ]

    out('  --- 断言 ---')
    for name, good, detail_txt in checks:
        out(f"    [{'PASS' if good else 'FAIL'}] {name:<28} {detail_txt}")
    for e in r.get('errors') or []:
        out(f"  ERROR: {e}")
    if SHOT_PATH.is_file():
        out(f"  截图: {SHOT_PATH} ({SHOT_PATH.stat().st_size} bytes)")
    out()

    all_ok = all(g for _, g, _ in checks)
    out('[PASS] 桌宠本体 ui.html 渲染 Live2D 成功。' if all_ok
        else '[FAIL] 桌宠本体未通过 —— 见上面的 FAIL 行。')
    return 0 if all_ok else 1


if __name__ == '__main__':
    try:
        code = main()
    except Exception:
        import traceback
        out(traceback.format_exc())
        code = 3
    sys.exit(code)
