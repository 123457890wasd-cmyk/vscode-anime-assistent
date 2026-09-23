"""
matrix.py — WebView2 WebGL 触发条件矩阵

已知：WebView2 里 getContext("webgl") 会让渲染器卡死（2D canvas 正常）。
这个脚本跑一组窗口配置对照，回答两件事：
  1. 是不是"透明窗口"这一项导致的？
  2. 加上 Chromium 的软件渲染开关后能否恢复？

每组配置各起一次真实窗口，读回 trace.jsonl 看最后到达哪一步。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "matrix_result.txt"
VENV_PY = Path(r"C:\Users\Mr.hancard\.workbuddy\binaries\python\envs\live2d_probe\Scripts\python.exe")

CONFIGS = [
    {
        "label": "A 基线（透明+置顶，与桌宠同参数）",
        "transparent": "1", "ontop": "1", "args": "",
    },
    {
        "label": "B 不透明",
        "transparent": "0", "ontop": "1", "args": "",
    },
    {
        "label": "C 透明 + enable-unsafe-swiftshader",
        "transparent": "1", "ontop": "1",
        "args": "--enable-unsafe-swiftshader",
    },
    {
        "label": "D 不透明 + swiftshader + ignore-gpu-blocklist",
        "transparent": "0", "ontop": "1",
        "args": "--enable-unsafe-swiftshader --ignore-gpu-blocklist",
    },
    {
        "label": "E 透明 + use-angle=swiftshader",
        "transparent": "1", "ontop": "1",
        "args": "--use-angle=swiftshader --enable-unsafe-swiftshader",
    },
]

_log = []


def out(m=""):
    _log.append(m)
    try:
        print(m)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode("ascii"))


def flush():
    try:
        LOG.write_text("\n".join(_log) + "\n", encoding="utf-8")
    except OSError:
        pass


def last_step(trace: Path):
    """返回 (最后一步名, 步骤数, 是否到达 all-done)"""
    if not trace.is_file():
        return ("(无任何回报)", 0, False)
    steps = []
    for ln in trace.read_text(encoding="utf-8").splitlines():
        try:
            steps.append(json.loads(ln))
        except Exception:
            pass
    if not steps:
        return ("(空)", 0, False)
    names = [s.get("step") for s in steps]
    return (names[-1], len(names), "all-done" in names)


def main():
    out("=" * 78)
    out("WebView2 WebGL 触发条件矩阵  (page=smoke.html)")
    out("=" * 78)
    out()

    rows = []
    for i, cfg in enumerate(CONFIGS, start=1):
        out("-" * 78)
        out(f"[{i}/{len(CONFIGS)}] {cfg['label']}")
        out("-" * 78)

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PROBE_PAGE"] = "smoke.html"
        env["PROBE_PORT"] = str(19900 + i)
        env["PROBE_TIMEOUT"] = "45"
        env["PROBE_TRANSPARENT"] = cfg["transparent"]
        env["PROBE_ONTOP"] = cfg["ontop"]
        env["PROBE_WEBVIEW_ARGS"] = cfg["args"]
        env["PROBE_LABEL"] = cfg["label"]

        try:
            subprocess.run(
                [str(VENV_PY), str(HERE / "run_webview.py")],
                env=env, cwd=str(HERE),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=150,
            )
        except subprocess.TimeoutExpired:
            out("  [warn] run_webview 超时")

        step, count, done = last_step(HERE / "trace.jsonl")
        gl_info = ""
        # 从 trace 里摘出 webgl 探测结论
        if (HERE / "trace.jsonl").is_file():
            for ln in (HERE / "trace.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                if rec.get("step", "").startswith("t2-webgl1-done"):
                    gl_info = json.dumps(rec.get("data"), ensure_ascii=False)
        out(f"  最后一步 : {step}")
        out(f"  步骤数   : {count}")
        out(f"  到 all-done: {done}")
        if gl_info:
            out(f"  webgl1   : {gl_info}")
        out()
        rows.append((cfg["label"], step, count, done, gl_info))

    out("=" * 78)
    out("汇总")
    out("=" * 78)
    for label, step, count, done, gl in rows:
        mark = "OK  " if done else "FAIL"
        out(f"  [{mark}] {label}")
        out(f"          最后到达: {step}  (共 {count} 步)")
        if gl:
            out(f"          webgl1: {gl}")
    out()
    flush()


if __name__ == "__main__":
    main()
