"""bisect_window.py — 二分定位哪个窗口参数让 WebView2 失效"""

import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = Path(r"C:\Users\Mr.hancard\.workbuddy\binaries\python\envs\live2d_probe\Scripts\python.exe")
LOG = HERE / "bisect_result.txt"
# 子进程把结果写在这里；注意别和自己这份日志搞混
CHILD_LOG = HERE / "tiny_result.txt"

CONFIGS = [
    ("1  朴素 html=",                    dict(TW_FRAMELESS="0", TW_TRANSPARENT="0", TW_ONTOP="0", TW_USE_URL="0")),
    ("2  仅 frameless",                  dict(TW_FRAMELESS="1", TW_TRANSPARENT="0", TW_ONTOP="0", TW_USE_URL="0")),
    ("3  仅 transparent",                dict(TW_FRAMELESS="0", TW_TRANSPARENT="1", TW_ONTOP="0", TW_USE_URL="0")),
    ("4  仅 on_top",                     dict(TW_FRAMELESS="0", TW_TRANSPARENT="0", TW_ONTOP="1", TW_USE_URL="0")),
    ("5  frameless+transparent",         dict(TW_FRAMELESS="1", TW_TRANSPARENT="1", TW_ONTOP="0", TW_USE_URL="0")),
    ("6  frameless+transparent+on_top",  dict(TW_FRAMELESS="1", TW_TRANSPARENT="1", TW_ONTOP="1", TW_USE_URL="0")),
    ("7  朴素 + url=",                   dict(TW_FRAMELESS="0", TW_TRANSPARENT="0", TW_ONTOP="0", TW_USE_URL="1")),
    ("8  三者 + url= (桌宠同参数)",       dict(TW_FRAMELESS="1", TW_TRANSPARENT="1", TW_ONTOP="1", TW_USE_URL="1")),
]

_lines = []


def out(m=""):
    _lines.append(m)
    try:
        print(m)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode("ascii"))
    try:
        LOG.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def parse(log_text):
    m = re.search(r"RESULT loaded=(\d) shown=(\d)", log_text or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def main():
    out("=" * 74)
    out("窗口参数二分：哪个参数让 WebView2 失效")
    out("=" * 74)
    out()

    rows = []
    for i, (label, env_extra) in enumerate(CONFIGS, start=1):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["TW_HARD"] = "22"
        env["TW_PORT"] = str(19990 + i)
        env["TW_TAG"] = label
        env.update(env_extra)

        try:
            subprocess.run([str(VENV_PY), str(HERE / "tiny_pywebview.py")],
                           env=env, cwd=str(HERE),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=90)
        except subprocess.TimeoutExpired:
            pass

        txt = CHILD_LOG.read_text(encoding="utf-8") if CHILD_LOG.is_file() else ""
        loaded, shown = parse(txt)
        if loaded is None:
            verdict = "无输出"
        elif loaded and shown:
            verdict = "OK"
        elif shown and not loaded:
            verdict = "shown 但未 loaded"
        else:
            verdict = "无事件"

        out(f"  {label:<34} loaded={loaded} shown={shown}   {verdict}")
        rows.append((label, loaded, shown, verdict))

    out()
    out("=" * 74)
    out("汇总")
    out("=" * 74)
    for label, loaded, shown, verdict in rows:
        flag = "OK  " if (loaded and shown) else "FAIL"
        out(f"  [{flag}] {label:<34} {verdict}")
    out()
    bad = [r[0] for r in rows if not (r[1] and r[2])]
    if bad:
        out("  失效配置:")
        for b in bad:
            out("    - " + b)
    out()


if __name__ == "__main__":
    main()
