"""
verify_headless.py — 在无头 Chromium 里跑通整条 Live2D pipeline

流程：预检模型闭包 -> 起 HTTP 服务 -> 无头浏览器打开探针页
      -> 等页面 POST 回报告 -> 逐项断言 -> 打印结论

不依赖 CDP / playwright：探针页自己把测量结果和截图 POST 回来。

用法：
    python verify_headless.py

产物：
    report.json        页面回报的原始测量数据
    shot.png           页面自己导出的渲染截图
    verify_result.txt  本次运行的完整 UTF-8 日志
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Windows 控制台编码不可靠（表情名是中文），日志自己落盘一份 UTF-8
LOG_PATH = HERE / "verify_result.txt"
_log_lines = []


def out(msg=""):
    _log_lines.append(msg)
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


def flush_log():
    try:
        LOG_PATH.write_text("\n".join(_log_lines) + "\n", encoding="utf-8")
    except OSError:
        pass


sys.path.insert(0, str(HERE))
import serve as probe_server  # noqa: E402

PORT = int(os.environ.get("PROBE_PORT", "19877"))
URL = f"http://127.0.0.1:{PORT}/"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser():
    for p in BROWSERS:
        if Path(p).is_file():
            return p
    return None


def banner(title):
    out("=" * 74)
    out(title)
    out("=" * 74)


def main():
    banner("STEP 1 / preflight — model reference closure")
    ok, lines = probe_server.preflight()
    for ln in lines:
        out("  " + ln)
    if not ok:
        out("\n[FAIL] model closure incomplete — fix the model files first.")
        flush_log()
        return 2
    out("  => closure OK")
    out()

    banner("STEP 2 / serve over http://127.0.0.1 (same-origin, no file://)")
    for stale in (".done", "report.json", "shot.png"):
        p = HERE / stale
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
    probe_server.serve(PORT)
    out(f"  listening on {URL}")

    browser = find_browser()
    if browser is None:
        out("[FAIL] no Chrome/Edge found")
        flush_log()
        return 2
    out(f"  browser: {browser}")
    out()

    banner("STEP 3 / run the probe page headless")
    profile = tempfile.mkdtemp(prefix="l2dprobe-")
    args = [
        browser,
        "--headless=new",
        "--disable-extensions",
        "--disable-background-networking",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--mute-audio",
        "--window-size=300,420",
        "--enable-unsafe-swiftshader",  # 无头环境下保证 WebGL 可用
        f"--user-data-dir={profile}",
        URL,
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    done_flag = HERE / ".done"
    deadline = time.time() + 90
    while time.time() < deadline and not done_flag.exists():
        if proc.poll() is not None:
            out("  [warn] browser exited early")
            break
        time.sleep(0.4)
    time.sleep(0.6)
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    shutil.rmtree(profile, ignore_errors=True)
    out()

    banner("STEP 4 / result")
    rp = HERE / "report.json"
    if not rp.is_file():
        out("[FAIL] no report came back — page never finished.")
        flush_log()
        return 2

    r = json.loads(rp.read_text(encoding="utf-8"))

    gl = r.get("gl") or {}
    m = r.get("model") or {}
    mo = r.get("motions") or {}
    ex = r.get("expressions") or []

    out(f"  stage      : {r.get('stage')}")
    out(f"  elapsed    : {r.get('elapsedMs')} ms")
    out(f"  vendor keys: {r.get('vendorKeys')}")
    out(f"  GL         : {gl.get('version')}  contextLost={gl.get('contextLost')}")
    out(f"  GL renderer: {gl.get('renderer')}")
    if m:
        out(f"  model      : intrinsic={m.get('intrinsicWidth')}x{m.get('intrinsicHeight')}"
            f"  fit={m.get('fitScale')}  textures={m.get('textures')}")
    if mo:
        out(f"  motions    : {len(mo)} groups -> {', '.join(mo.keys())}")
    out(f"  expressions: {len(ex)}")
    out()

    checks = []

    px = r.get("paint") or {}
    if px:
        good = (px.get("ratio") or 0) >= 0.05
        checks.append(("静态帧画出内容", good,
                       f"opaque={px.get('opaque')} semi={px.get('semi')} "
                       f"ratio={px.get('ratio')}"))

    ma = r.get("motionAnim") or {}
    if ma:
        good = (ma.get("changedPixels") or 0) > 0
        checks.append(("idle 动作驱动画面", good,
                       f"{ma.get('changedPixels')} px 变化 "
                       f"({(ma.get('ratio') or 0) * 100:.2f}%)"))

    et = r.get("exprToggle") or {}
    if et:
        good = (et.get("changedPixels") or 0) > 0
        checks.append(("表情能改变画面", good,
                       f"'{et.get('name')}' -> {et.get('changedPixels')} px "
                       f"({(et.get('ratio') or 0) * 100:.2f}%)"))

    er = r.get("exprRestore") or {}
    df = r.get("driftFloor") or {}
    if er and df:
        residual = er.get("residualPixels") or 0
        floor = df.get("changedPixels") or 0
        tol = max(floor * 3, 1)
        good = residual <= tol
        checks.append(("表情可还原（对比物理漂移）", good,
                       f"残差={residual} px / 物理漂移基准={floor} px / 上限={tol}"))

    sw = r.get("motionSwitch") or {}
    if sw:
        good = bool(sw.get("started")) and (sw.get("changedPixels") or 0) > 0
        checks.append(("非 idle 动作可播放", good,
                       f"'{sw.get('group')}' started={sw.get('started')} "
                       f"changed={sw.get('changedPixels')} px"))

    out("  --- 明细 ---")
    if px:
        out(f"  静态帧     : opaque={px.get('opaque')} semi={px.get('semi')} "
            f"total={px.get('total')} ratio={px.get('ratio')}")
    if ma:
        out(f"  idle 动画  : {ma.get('changedPixels')} px 变化 "
            f"({(ma.get('ratio') or 0) * 100:.2f}%)")
    if df:
        out(f"  物理漂移   : {df.get('changedPixels')} px / {df.get('windowFrames')} 帧 "
            f"({(df.get('ratio') or 0) * 100:.2f}%)")
    if et:
        out(f"  表情施加   : '{et.get('name')}' -> {et.get('changedPixels')} px "
            f"({(et.get('ratio') or 0) * 100:.2f}%)")
    if er:
        out(f"  表情还原   : resetExpression={er.get('hadResetApi')} "
            f"残差={er.get('residualPixels')} px ({(er.get('ratio') or 0) * 100:.2f}%)")
    if sw:
        out(f"  动作切换   : '{sw.get('group')}' started={sw.get('started')} "
            f"changed={sw.get('changedPixels')} px")

    for e in r.get("errors") or []:
        out(f"  ERROR      : {e}")

    shot = HERE / probe_server.SHOT_NAME
    if shot.is_file():
        out(f"  截图       : {shot}  ({shot.stat().st_size} bytes)")

    out()
    out("  --- 断言 ---")
    for name, good, detail in checks:
        out(f"    [{'PASS' if good else 'FAIL'}] {name:<24} {detail}")

    all_good = bool(r.get("ok")) and all(g for _, g, _ in checks)
    out()
    if all_good:
        out("[PASS] pipeline 全通：core + vendor + 模型 + 动作 + 表情。")
        flush_log()
        return 0
    out("[FAIL] pipeline 未完全通过，见上面的 stage / errors。")
    flush_log()
    return 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        import traceback
        out(traceback.format_exc())
        flush_log()
        code = 3
    flush_log()
    sys.exit(code)
