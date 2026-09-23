"""
run_webview.py — 在真实目标环境（pywebview + WebView2）里跑同一条 pipeline

Chrome 无头能通过不代表 WebView2 能通过：渲染栈、透明窗口、脚本注入策略
都不是一回事。这个脚本开一个与 Airi 桌宠同参数的窗口
（frameless + transparent + on_top），让探针页自己回报结果后自动关闭。

用法：
    <venv>\\Scripts\\python.exe run_webview.py

产物：
    report_webview.json      页面回报的原始测量数据
    shot_webview.png         页面自己导出的渲染截图
    webview_result.txt       本次运行的完整 UTF-8 日志
"""

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_PATH = HERE / "webview_result.txt"
_log = []


def out(msg=""):
    """打印一行，并**立刻**落盘。

    2026-09-22 实测教训：用户跑完 pass A 后关掉了控制台窗口，进程被硬杀，
    只在退出路径上调用的 flush_log() 再也不会执行 —— 结果 `webview_result.txt`
    和 `result_A_opaque.txt` 都没生成，只剩下服务器线程早先写好的 report.json /
    shot.png（那两行是它们自己写的，所以留下来了）。日志必须随写随落盘。
    """
    _log.append(msg)
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))
    flush_log()


def flush_log():
    """整份重写。文件只有几 KB，每次 out() 都调也没问题。"""
    try:
        LOG_PATH.write_text("\n".join(_log) + "\n", encoding="utf-8")
    except OSError:
        pass


# WebView2 这一次的截图要写成独立文件名，**必须在 import serve 之前设**：
# serve 在模块级读 PROBE_SHOT_NAME，默认值 shot.png 是 Chrome 那一侧的名字，
# 两边共用会互相覆盖（已经踩过两次）。
os.environ.setdefault("PROBE_SHOT_NAME", "shot_webview.png")

sys.path.insert(0, str(HERE))
import serve as probe_server  # noqa: E402

PORT = int(os.environ.get("PROBE_PORT", "19878"))
# PROBE_PAGE 可切到 smoke.html 做分层定位
PAGE = os.environ.get("PROBE_PAGE", "probe.html")
# 默认隐藏页面上的调试日志；PROBE_CLEAN=0 可以让它显示出来，便于肉眼判断
SHOW_LOG = os.environ.get("PROBE_CLEAN", "1") in ("0", "false", "no")
# PROBE_SHOWLOG=1 强制保留页面上的日志，且不用 showcase 的接管 ticker。
# 诊断 pass 就该这样：绿字日志会直接告诉你卡在哪一步，比看一张白屏有用得多。
FORCE_LOG = os.environ.get("PROBE_SHOWLOG", "0") in ("1", "true", "yes")
# 手动模式：探针跑完后把角色留在屏幕上 N 秒，方便肉眼确认渲染是否成功
MANUAL = os.environ.get("PROBE_MANUAL", "0") in ("1", "true", "yes")
# 手动模式下角色在屏上停留的秒数
HOLD_S = float(os.environ.get("PROBE_HOLD", "60"))
TIMEOUT_S = int(os.environ.get("PROBE_TIMEOUT", "90" if MANUAL else "60"))
# 早停阈值：多久没有新的回报才认为卡住。冷启动可能较慢，别设太紧。
STALL_S = float(os.environ.get("PROBE_STALL", "20"))
# 兜底硬超时：WebView2 出问题时 webview.start() 可能永不返回，那样批处理会永久
# 挂在这一行、透明僵尸窗口也关不掉。守护线程到点强制退出进程。
HARD_S = float(os.environ.get("PROBE_HARD_TIMEOUT", str(TIMEOUT_S + 30)))

_qs = []
if PAGE == "probe.html":
    if FORCE_LOG:
        pass                          # 日志常显，不做任何接管
    elif MANUAL:
        _qs.append("showcase=1")      # 跑完留住窗口、持续播放 idle
    elif not SHOW_LOG:
        _qs.append("clean=1")
URL = f"http://127.0.0.1:{PORT}/{PAGE}" + ("?" + "&".join(_qs) if _qs else "")

# 窗口参数可调，便于做 A/B 对照（透明窗口是否就是 WebGL 卡死的原因）
TRANSPARENT = os.environ.get("PROBE_TRANSPARENT", "1") not in ("0", "false", "no")
ON_TOP = os.environ.get("PROBE_ONTOP", "1") not in ("0", "false", "no")
# 诊断时建议设为 0：无边框窗口既没有标题栏也没有内容时，用户只能去任务管理器杀进程
FRAMELESS = os.environ.get("PROBE_FRAMELESS", "1") not in ("0", "false", "no")
# 通过 WebView2 的官方环境变量透传 Chromium 开关
EXTRA_ARGS = os.environ.get("PROBE_WEBVIEW_ARGS", "").strip()
LABEL = os.environ.get("PROBE_LABEL", "")


def main():
    out("=" * 74)
    out("WebView2 / pywebview 目标环境验证" + (f"  [{LABEL}]" if LABEL else ""))
    out("=" * 74)
    out(f"  transparent={TRANSPARENT}  on_top={ON_TOP}  frameless={FRAMELESS}")
    out(f"  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS={EXTRA_ARGS or '(none)'}")

    if EXTRA_ARGS:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = EXTRA_ARGS

    try:
        import webview
    except ImportError:
        out("[FAIL] pywebview 未安装")
        flush_log()
        return 2
    out(f"  pywebview: {getattr(webview, '__version__', 'installed')}")

    ok, lines = probe_server.preflight()
    if not ok:
        for ln in lines:
            out("  " + ln)
        out("[FAIL] 模型闭包不完整")
        flush_log()
        return 2
    out("  模型闭包: OK (57/57)")

    for stale in (".done", "report.json", "shot.png", "hits.log", "trace.jsonl"):
        p = HERE / stale
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    probe_server.serve(PORT)
    out(f"  服务: {URL}")

    # 与 desktop_pet/standalone.py 相同的窗口参数
    WIN_W, WIN_H = 300, 480
    window = webview.create_window(
        title="Live2D WebView2 probe",
        url=URL,
        width=WIN_W, height=WIN_H,
        x=120, y=120,
        frameless=True,
        transparent=TRANSPARENT,
        on_top=ON_TOP,
        resizable=False,
        easy_drag=False,
    )

    result = {"closed": False, "reason": None}

    def watchdog():
        """早停：冒烟页每步回报，出现 all-done 或停止增长就收工，不必等满超时。"""
        done = HERE / ".done"
        trace = HERE / "trace.jsonl"
        deadline = time.time() + TIMEOUT_S
        last_size = -1
        last_change = time.time()
        while time.time() < deadline:
            if done.exists():
                result["reason"] = "report received"
                break
            txt = ""
            size = 0
            if trace.is_file():
                try:
                    txt = trace.read_text(encoding="utf-8")
                    size = len(txt)
                except OSError:
                    pass
            if '"all-done"' in txt:
                result["reason"] = "trace complete"
                break
            if size != last_size:
                last_size = size
                last_change = time.time()
            elif time.time() - last_change > STALL_S:
                result["reason"] = f"trace stalled {STALL_S:.0f}s"
                break
            time.sleep(0.25)
        else:
            result["reason"] = "timeout"
        time.sleep(0.6)
        result["closed"] = True
        try:
            window.destroy()
        except Exception as exc:
            out(f"  [warn] destroy: {exc!r}")

    def manual_watch():
        """手动模式：等页面回报；回报后让角色在屏上停留 HOLD_S 秒再关窗。

        超时也一定关窗 —— 这个窗口是无边框 + 透明的，一旦里面什么都没画出来
        用户既看不见它、也没有标题栏可以关，只能去任务管理器杀进程。
        """
        done = HERE / ".done"
        deadline = time.time() + TIMEOUT_S
        while time.time() < deadline:
            if done.exists():
                result["reason"] = f"report received, held {HOLD_S:.0f}s"
                out(f"  页面已回报 —— 角色应在屏幕上，{HOLD_S:.0f} 秒后自动关窗")
                time.sleep(HOLD_S)
                break
            time.sleep(0.3)
        else:
            result["reason"] = "timeout (页面未回报)"
        time.sleep(0.4)
        result["closed"] = True
        try:
            window.destroy()
        except Exception:
            pass

    threading.Thread(target=manual_watch if MANUAL else watchdog, daemon=True).start()

    # 兜底硬超时：WebView2 出问题时 webview.start() 可能永不返回，
    # 那样批处理会永久挂在这一行、透明僵尸窗口也关不掉。守护线程强制退出进程。
    HARD_S = float(os.environ.get("PROBE_HARD_TIMEOUT", str(TIMEOUT_S + 30)))

    def hard_kill():
        time.sleep(HARD_S)
        out(f"[WARN] 硬超时 {HARD_S:.0f}s —— 强制结束进程（说明窗口线程卡死了）")
        flush_log()
        os._exit(4)

    threading.Thread(target=hard_kill, daemon=True).start()

    out("  打开窗口（真实 WebView2）…")
    try:
        webview.start(debug=False)
    except Exception as exc:
        out(f"[FAIL] webview.start raised: {exc!r}")
        flush_log()
        return 3

    out(f"  窗口已关闭 ({result['reason']})")
    out()

    hits = HERE / "hits.log"
    if hits.is_file():
        out("  --- 服务器收到的请求 ---")
        for ln in hits.read_text(encoding="utf-8").splitlines():
            out("    " + ln)
        out()
    else:
        out("  --- 服务器一个请求都没收到 ---")
        out()

    trace = HERE / "trace.jsonl"
    if trace.is_file():
        out("  --- 页面增量追踪 (trace.jsonl) ---")
        for ln in trace.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(ln)
            except Exception:
                out("    " + ln)
                continue
            body = json.dumps(rec.get("data"), ensure_ascii=False)
            out(f"    #{rec.get('seq')} +{rec.get('t')}ms  {rec.get('step')}  {body}")
        out()

    rp = HERE / "report.json"
    if not rp.is_file():
        out("[FAIL] 没有收到最终报告 —— 页面中途卡住了，见上面的 trace")
        flush_log()
        return 2

    r = json.loads(rp.read_text(encoding="utf-8"))
    # 与无头那次区分开，留一份独立的产物
    try:
        shutil.copyfile(rp, HERE / "report_webview.json")
    except OSError:
        pass

    gl = r.get("gl") or {}
    m = r.get("model") or {}
    mo = r.get("motions") or {}
    ex = r.get("expressions") or []

    # ---- 冒烟模式：只回报环境与依赖加载能力，用于分层定位 ----------
    if "tag" in r:
        out("=" * 74)
        out("冒烟结果（分层定位）")
        out("=" * 74)
        env = r.get("env") or {}
        out(f"  href       : {env.get('href')}")
        out(f"  origin     : {env.get('origin')}  protocol={env.get('protocol')}")
        out(f"  viewport   : {env.get('viewport')}  dpr={env.get('dpr')}")
        out(f"  ua         : {str(env.get('ua'))[:100]}")
        g2 = (r.get("gl") or {}).get("webgl2") or {}
        g1 = (r.get("gl") or {}).get("webgl1") or {}
        out(f"  webgl2     : available={g2.get('available')} "
            f"{g2.get('version') or g2.get('reason')}")
        out(f"  webgl2 rend: {g2.get('renderer')}")
        out(f"  webgl2 read: {g2.get('readback')}")
        out(f"  webgl1     : available={g1.get('available')} "
            f"{g1.get('version') or g1.get('reason')}")
        out(f"  core fetch : {r.get('core')}")
        out(f"  core global: {r.get('coreLoaded')}")
        out(f"  vendor     : {r.get('vendor')}")
        out()
        out("  --- steps ---")
        for s in r.get("steps") or []:
            out("    " + s)
        for e in r.get("errors") or []:
            out(f"  ERROR      : {e}")
        out()
        out(f"  tag        : {r.get('tag')}")
        flush_log()
        return 0 if r.get("ok") else 1

    out("=" * 74)
    out("结果")
    out("=" * 74)
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
        checks.append(("静态帧画出内容", (px.get("ratio") or 0) >= 0.05,
                       f"opaque={px.get('opaque')} ratio={px.get('ratio')}"))

    ma = r.get("motionAnim") or {}
    if ma:
        checks.append(("idle 动作驱动画面", (ma.get("changedPixels") or 0) > 0,
                       f"{ma.get('changedPixels')} px 变化"))

    et = r.get("exprToggle") or {}
    if et:
        checks.append(("表情能改变画面", (et.get("changedPixels") or 0) > 0,
                       f"'{et.get('name')}' -> {et.get('changedPixels')} px"))

    er = r.get("exprRestore") or {}
    df = r.get("driftFloor") or {}
    if er and df:
        residual = er.get("residualPixels") or 0
        floor = df.get("changedPixels") or 0
        tol = max(floor * 3, 1)
        checks.append(("表情可还原（对比物理漂移）", residual <= tol,
                       f"残差={residual} / 漂移基准={floor} / 上限={tol}"))

    sw = r.get("motionSwitch") or {}
    if sw:
        checks.append(("非 idle 动作可播放",
                       bool(sw.get("started")) and (sw.get("changedPixels") or 0) > 0,
                       f"'{sw.get('group')}' started={sw.get('started')}"))

    # WebView2 专属：得是真 WebGL，不能退化
    gl_ok = "WebGL" in str(gl.get("version", ""))
    checks.append(("WebView2 提供 WebGL 上下文", gl_ok, str(gl.get("version"))))
    checks.append(("WebGL 上下文未丢失", not gl.get("contextLost"),
                   f"contextLost={gl.get('contextLost')}"))

    for e in r.get("errors") or []:
        out(f"  ERROR      : {e}")

    for name in ("shot.png",):
        p = HERE / name
        if p.is_file():
            out(f"  {name}       : {p.stat().st_size} bytes")

    out()
    out("  --- 断言 ---")
    for name, good, detail in checks:
        out(f"    [{'PASS' if good else 'FAIL'}] {name:<26} {detail}")

    all_good = bool(r.get("ok")) and all(g for _, g, _ in checks)
    out()
    if all_good:
        out("[PASS] WebView2 目标环境验证通过。")
        flush_log()
        return 0
    out("[FAIL] WebView2 环境未完全通过。")
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
    sys.exit(code)
