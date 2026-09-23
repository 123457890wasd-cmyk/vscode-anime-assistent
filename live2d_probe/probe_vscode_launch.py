"""
probe_vscode_launch.py — 完全复刻「VS Code 扩展点右下角按钮」的启动条件

为什么要有这个
--------------
`src/extension.ts:launchStandalonePet()` 是这样起桌宠的：

    spawn(python, [extensionRoot/desktop_pet/standalone.py], {
        cwd: petDir,          // = desktop_pet/
        detached: true,
        stdio: 'ignore',      // ★ 所有输出被丢掉
        windowsHide: true,
    })
    python = AIRI_PYTHON_PATH || %USERPROFILE%\\AppData\\Local\\Programs\\Python\\Python312\\python.exe || "python"

所以「扩展能不能起桌宠」= 这个 Python + 这个 cwd 下 standalone.py 能不能跑起来。
我的 verify_pet_ui.py / smoke_pet_server.py 都是拿**别的 Python**（probe venv）
和**别的 cwd**（调用者 cwd）跑的，覆盖不到这条。

本脚本按上面完全一样的 Python 与 cwd 跑**真的 standalone.py**，把它的 stdout/stderr
抓回来（扩展丢了，我们不丢），看到 "UI served on" 或进程退出就收工，然后杀进程树。

注意：这会真的弹一下桌宠窗口（有 webview.start()）。看到结果就杀掉。
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PET = REPO / 'desktop_pet'
LOG_PATH = HERE / 'vscode_launch_result.txt'

WAIT_S = float(os.environ.get('PROBE_WAIT_S', '25'))

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


def _replicate_find_python():
    """照抄 extension.ts:findPythonPath() 的优先级。"""
    cands = [
        os.environ.get('AIRI_PYTHON_PATH'),
        str(Path(os.path.expanduser('~')) / 'AppData' / 'Local' / 'Programs'
            / 'Python' / 'Python312' / 'python.exe'),
        'python',
    ]
    for c in cands:
        if not c:
            continue
        if c == 'python' or os.path.exists(c):
            return c
    return 'python'


def _kill_tree(pid):
    try:
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       capture_output=True, timeout=20)
    except Exception as exc:
        out(f'  (taskkill 失败: {exc!r})')


def main():
    out('=' * 74)
    out('复刻 VS Code 扩展启动路径（真 standalone.py + 真 Python + 真 cwd）')
    out('=' * 74)

    python = _replicate_find_python()
    out(f'  extension.ts 会挑的 Python : {python}')

    # 复刻 findPythonPath 的判定顺序，说明为什么是它
    p312 = Path(os.path.expanduser('~')) / 'AppData' / 'Local' / 'Programs' / 'Python' / 'Python312' / 'python.exe'
    out(f'  （判定依据：AIRI_PYTHON_PATH={os.environ.get("AIRI_PYTHON_PATH")!r}，'
        f'Python312 存在={p312.exists()}）')

    script = PET / 'standalone.py'
    out(f'  脚本      : {script}')
    out(f'  cwd       : {PET}')
    out()

    if python == 'python':
        python = shutil.which('python') or 'python'
    env = dict(os.environ)
    # 让子进程别开 __pycache__（跟扩展无关，但保持干净）
    env['PYTHONDONTWRITEBYTECODE'] = '1'

    proc = subprocess.Popen(
        [python, str(script)],
        cwd=str(PET),                      # ★ 与扩展一致
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,          # 扩展丢了输出；我们合并抓回来
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
        env=env,
    )

    lines = []
    started = threading.Event()
    exited = threading.Event()

    def reader():
        for ln in proc.stdout:
            lines.append(ln.rstrip('\n'))
            if 'UI served on' in ln:
                started.set()
        exited.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        if started.is_set() or proc.poll() is not None:
            break
        time.sleep(0.25)

    # 再给窗口一点时间起来
    if started.is_set() and proc.poll() is None:
        time.sleep(3.0)

    alive = proc.poll() is None
    if alive:
        _kill_tree(proc.pid)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    t.join(timeout=3)

    out('  --- standalone.py 的输出（扩展会丢掉这些） ---')
    if not lines:
        out('    (没有任何输出)')
    for ln in lines:
        out('    ' + ln)
    out()

    joined = '\n'.join(lines)

    def has(pat):
        return re.search(pat, joined) is not None

    # 连接被 reset 是**我杀进程**造成的收尾噪声，不该算失败。
    # （正常路径下 standalone.py / common.py 的 handle_error 已经把它吞掉了，
    #   这里只在漏网时降级为提示，不算 FAIL。）
    reset_noise = has(r'ConnectionResetError|ConnectionAbortedError|BrokenPipeError')
    real_tb = has(r'Traceback \(most recent call last\)') and not reset_noise

    # 新加的落盘日志有没有真的写出来
    pet_log = PET / '.airi-pet.log'
    log_txt = ''
    if pet_log.is_file():
        try:
            log_txt = pet_log.read_text(encoding='utf-8')
        except OSError:
            log_txt = ''
    out('  --- desktop_pet/.airi-pet.log（新加的落盘日志） ---')
    if log_txt:
        for ln in log_txt.splitlines():
            out('    ' + ln)
    else:
        out('    (文件不存在或读不到)')
    out()

    checks = [
        ('进程没有立刻崩掉（能活到被杀）', alive or has('UI served on'),
         f'returncode={proc.returncode}'),
        ('没有意外的 traceback', not real_tb,
         '只有客户端断开噪声' if reset_noise else '干净'),
        ('没有 "pywebview not installed"', 'pywebview not installed' not in joined, ''),
        ('没有 "cannot start asset server"', 'cannot start asset server' not in joined, ''),
        ('Live2D 自检为 ENABLED', has(r'\[airi-live2d\] live2d ENABLED'),
         'ENABLED' if has(r'live2d ENABLED') else '未 ENABLED —— 会退回立绘'),
        ('闭包 57 个文件齐全', has(r'closure=57 files, 57 present, 0 missing'), ''),
        ('素材服务器起来了（UI served on）', has(r'UI served on http://127\.0\.0\.1:\d+/'), ''),
        ('用的确实是 Live2D，不是降级', has(r'live2d ENABLED')
         and not has(r'live2d disabled'), ''),
        ('诊断日志 .airi-pet.log 有内容', bool(log_txt.strip()),
         f'{len(log_txt)} 字符'),
        ('日志里记了 python 与 cwd', 'python  :' in log_txt and 'cwd     :' in log_txt, ''),
    ]

    out('  --- 断言 ---')
    for name, good, detail in checks:
        out(f"    [{'PASS' if good else 'FAIL'}] {name:<36} {detail}")

    all_ok = all(g for _, g, _ in checks)
    out()
    out(f'  通过 {sum(1 for _, g, _ in checks if g)}/{len(checks)}')
    out('[PASS] VS Code 扩展的启动路径可用。' if all_ok
        else '[FAIL] 扩展这条路有问题 —— 见上面 FAIL 行。')
    return 0 if all_ok else 1


if __name__ == '__main__':
    try:
        code = main()
    except Exception:
        import traceback
        out(traceback.format_exc())
        code = 3
    sys.exit(code)
