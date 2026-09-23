"""
check_launchers.py — 启动器（.bat）自检

为什么需要这个脚本
------------------
2026-09-22 实测踩坑：`.bat` 里写了中文（UTF-8 无 BOM），cmd.exe 用 OEM 码页
(936/GBK) 解码批处理文件，中文字节被拆开、吃掉后续换行，于是 cmd 试图把
'the chinese fragment' 当命令执行 —— 报出
    '90"' 不是内部或外部命令
    'xxx?echo' 不是内部或外部命令
用户双击后整个脚本解析崩掉，Python 一行都没跑，测试实际上从未执行。

结论：Windows 批处理文件必须**纯 ASCII**。中文只能出现在 Python 写出的
UTF-8 结果文件里。本脚本把这条规则变成断言。

另外检查两件容易漂移的事：
  1. bat 里 set 的环境变量名，Python 是否真的读（防拼写错误静默失效）
  2. 批处理里出现可能破坏 cmd 解析的裸字符（& | < > ^ " 之外）

输出：launcher_check.txt（UTF-8）
"""

import importlib.util
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "launcher_check.txt"
_lines = []


def say(msg=""):
    _lines.append(msg)
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


def flush():
    LOG.write_text("\n".join(_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. .bat 必须纯 ASCII
# ---------------------------------------------------------------------------

# 允许出现在 bat 里、不构成解析风险的字符之外，重点找这些裸字符
RISKY = set("&|<>^")


def check_ascii(bat: Path):
    raw = bat.read_bytes()
    bad_bytes = [(i, b) for i, b in enumerate(raw) if b > 0x7F]
    text = raw.decode("ascii", "replace")
    lines = text.splitlines()

    say(f"  {bat.name}")
    say(f"    大小 {len(raw)} 字节 / {len(lines)} 行")

    if bad_bytes:
        say(f"    [FAIL] 含 {len(bad_bytes)} 个非 ASCII 字节 —— cmd 会误解码！")
        # 打印第一处上下文，便于定位
        i, b = bad_bytes[0]
        lo = max(0, i - 40)
        ctx = raw[lo:i + 40].decode("utf-8", "replace")
        say(f"           首处偏移 {i} (0x{b:02x}) 上下文: {ctx!r}")
        return False

    say("    [PASS] 纯 ASCII")

    # 裸风险字符：必须都在引号内，或者是我们有意使用的重定向/续行
    issues = []
    for n, ln in enumerate(lines, 1):
        stripped = ln.strip()
        if not stripped or stripped.startswith("REM") or stripped.startswith("::"):
            continue
        # 去掉引号内的内容后再看还有没有裸风险字符
        outside = re.sub(r'"[^"]*"', '""', ln)
        # >nul / >> 是合法重定向
        outside = outside.replace(">nul", "").replace(">>", "")
        for ch in RISKY:
            if ch in outside:
                issues.append((n, ch, stripped))
    if issues:
        say(f"    [WARN] {len(issues)} 处引号外的风险字符（逐个人工确认）:")
        for n, ch, s in issues[:10]:
            say(f"           行 {n}  字符 {ch!r}  {s}")
    else:
        say("    [PASS] 引号外无风险字符")
    return True


# ---------------------------------------------------------------------------
# 2. bat 设置的环境变量，Python 是否真的读
# ---------------------------------------------------------------------------

def python_read_vars(py_files):
    """扫出 os.environ.get("NAME") / os.environ["NAME"] 里的变量名。"""
    names = set()
    pat = re.compile(r'os\.environ(?:\.get\(\s*|\[\s*)["\']([A-Za-z_][A-Za-z0-9_]*)["\']')
    for f in py_files:
        names |= set(pat.findall(f.read_text(encoding="utf-8")))
    return names


def bat_set_vars(bat: Path):
    """提取 set "NAME=VALUE" / set NAME=VALUE 的变量名。"""
    names = {}
    pat = re.compile(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=', re.IGNORECASE)
    for n, ln in enumerate(bat.read_text(encoding="ascii", errors="replace").splitlines(), 1):
        m = pat.match(ln)
        if m:
            names.setdefault(m.group(1).upper(), n)
    return names


# 标准变量，不走 os.environ.get
ALLOWED_STANDARD = {"PYTHONUTF8", "PYTHONIOENCODING", "PYTHONPATH", "PY"}


# ---------------------------------------------------------------------------
# 3. 本地导入 run_webview，验证环境变量真的被解析成期望的窗口参数
# ---------------------------------------------------------------------------

def load_run_webview(env):
    """干净地重新加载 run_webview，按给定环境解析模块级常量。

    先清掉所有 PROBE_* / TW_*，否则外层 shell 里残留的同名变量会造成假通过。
    """
    for k in [k for k in os.environ if k.startswith(("PROBE_", "TW_"))]:
        del os.environ[k]
    for k, v in env.items():
        os.environ[k] = v
    spec = importlib.util.spec_from_file_location(
        "run_webview_check", HERE / "run_webview.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    say("=" * 74)
    say("启动器自检 (.bat)")
    say("=" * 74)
    say()

    bats = sorted(HERE.glob("*.bat"))
    if not bats:
        say("[FAIL] 目录里没有 .bat")
        flush()
        return 1

    say("-- 1. 编码：批处理必须纯 ASCII --")
    ascii_ok = all(check_ascii(b) for b in bats)
    say()

    say("-- 2. 环境变量契约：bat 设置的变量 Python 是否读 --")
    py_files = [p for p in HERE.glob("*.py") if p.name != Path(__file__).name]
    read_vars = python_read_vars(py_files)
    say(f"  Python 侧读取的变量 ({len(read_vars)}): {', '.join(sorted(read_vars)) or '(none)'}")
    say()
    contract_ok = True
    for bat in bats:
        set_vars = bat_set_vars(bat)
        unknown = [v for v in set_vars if v not in read_vars and v not in ALLOWED_STANDARD]
        tag = "[PASS]" if not unknown else "[FAIL]"
        say(f"  {tag} {bat.name}: 设置 {len(set_vars)} 个变量")
        if unknown:
            contract_ok = False
            for v in unknown:
                say(f"          bat 第 {set_vars[v]} 行设了 {v}，但没有任何 Python 脚本读它"
                    "  <- 拼写错误？")
    say()

    say("-- 3. 窗口参数解析（模拟 pass A / pass B） --")
    plumb_ok = True
    for label, env, want, want_url in (
        ("pass A opaque", {"PROBE_PORT": "19901", "PROBE_TRANSPARENT": "0",
                           "PROBE_ONTOP": "0", "PROBE_FRAMELESS": "0",
                           "PROBE_MANUAL": "1", "PROBE_HOLD": "40",
                           "PROBE_TIMEOUT": "70", "PROBE_LABEL": "pass A opaque",
                           "PROBE_SHOWLOG": "1"},
         {"TRANSPARENT": False, "ON_TOP": False, "FRAMELESS": False,
          "MANUAL": True, "HOLD_S": 40.0, "TIMEOUT_S": 70},
         # pass A 必须保留日志：没有 showcase、也没有 clean
         "http://127.0.0.1:19901/probe.html"),
        ("pass B transparent", {"PROBE_PORT": "19902", "PROBE_TRANSPARENT": "1",
                                "PROBE_ONTOP": "1", "PROBE_FRAMELESS": "1",
                                "PROBE_MANUAL": "1", "PROBE_HOLD": "40",
                                "PROBE_TIMEOUT": "70", "PROBE_LABEL": "pass B transparent",
                                "PROBE_SHOWLOG": "0"},
         {"TRANSPARENT": True, "ON_TOP": True, "FRAMELESS": True,
          "MANUAL": True, "HOLD_S": 40.0, "TIMEOUT_S": 70},
         # pass B 用 showcase：擦干净画面、持续播 idle
         "http://127.0.0.1:19902/probe.html?showcase=1"),
    ):
        mod = load_run_webview(env)
        got = {k: getattr(mod, k) for k in want}
        ok = got == want and mod.URL == want_url
        plumb_ok &= ok
        say(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        say(f"         url        : {mod.URL}")
        say(f"         transparent={mod.TRANSPARENT} on_top={mod.ON_TOP} "
            f"frameless={mod.FRAMELESS}")
        say(f"         hold={mod.HOLD_S}s timeout={mod.TIMEOUT_S}s "
            f"hard={mod.HARD_S}s")
        if not ok:
            say(f"         期望 {want}")
            say(f"         实际 {got}")
            say(f"         期望 url {want_url}")
            say(f"         实际 url {mod.URL}")
    say()

    say("-- 4. venv 与静态资源 --")
    venv_py = Path(r"C:\Users\Mr.hancard\.workbuddy\binaries\python\envs\live2d_probe\Scripts\python.exe")
    say(f"  venv python : {'[PASS]' if venv_py.is_file() else '[FAIL]'} {venv_py}")
    need = ["probe.html", "serve.py", "run_webview.py", "vendor/live2d-vendor.js",
            "vendor/live2dcubismcore.min.js", "model/ds-whale-girl/c_0120.model3.json"]
    missing = [n for n in need if not (HERE / n).is_file()]
    say(f"  静态资源    : {'[PASS] 全部存在' if not missing else '[FAIL] 缺 ' + ', '.join(missing)}")
    say()

    os_ok = ascii_ok and contract_ok and plumb_ok and venv_py.is_file() and not missing
    say("=" * 74)
    say("[PASS] 启动器自检通过" if os_ok else "[FAIL] 启动器自检未通过 —— 见上面 FAIL 行")
    say("=" * 74)
    flush()
    return 0 if os_ok else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        import traceback
        say(traceback.format_exc())
        flush()
        code = 3
    sys.exit(code)
