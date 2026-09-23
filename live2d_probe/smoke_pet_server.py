"""
smoke_pet_server.py — 冒烟测试桌宠**生产**静态服务器入口

为什么单独要这个
----------------
`verify_pet_ui.py` 走的是 `pet_common._make_handler()`（真的处理器，但服务器是它
自己 new 的）。而 `standalone.py` / `main.py` 实际调用的是
`pet_common.start_asset_server(html)` —— 这个**绑定端口 + 起线程**的包装函数
没被任何测试覆盖过。它一挂，桌宠就直接退出（返回 (None, 0) 时上层会 sys.exit）。

这里就补这一段，全部通过真 HTTP 请求验证：

1. `start_asset_server()` 能拿到可用端口
2. `/` 返回注入好的 ui.html
3. `/favicon.ico` 是 204（HTTP/1.1 下 204 不能带 Content-Length）
4. `/vendor/*` 两个 js 都能取到，且长度与磁盘一致
5. **整个模型闭包**（model3.json 引用到的每一个文件）都能 200 拿到
6. **路径越界必须被挡住**：`..`、URL 编码的 `%2e%2e`、Windows 反斜杠
7. 未知路径是 404，不是 200

产物：pet_server_result.txt
"""

import http.client
import json
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PET = REPO / 'desktop_pet'
LOG_PATH = HERE / 'pet_server_result.txt'

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


def fetch(port, raw_path):
    """用 http.client 直发**原始**路径 —— urlopen 可能会规范化掉 `..`，
    那样就测不出越界防护了。返回 (状态码, headers, body)。"""
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        conn.request('GET', raw_path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, dict(resp.getheaders()), body
    finally:
        conn.close()


def main():
    out('=' * 74)
    out('桌宠生产静态服务器冒烟测试（start_asset_server + 真 HTTP）')
    out('=' * 74)

    # ---- 前置：素材自检（顺带把 Cubism Core 补齐） ----
    ok, lines = live2d_assets.check_assets()
    for ln in lines:
        out('  ' + ln)
    out(f'  check_assets -> {ok}')
    out()

    # ---- 按生产路径组装 HTML 并起服务 ----
    char_img = pet_common.find_character_image()
    html = pet_common.load_html(19876, char_img, live2d=ok)
    base, port = pet_common.start_asset_server(html)
    out(f'  start_asset_server -> {base!r} port={port}')
    if not port:
        out()
        out('[FAIL] start_asset_server 没给出端口，桌宠启动会直接退出。')
        return 1

    out(f'  HTML 里 LIVE2D_ENABLED = true 注入: {"var LIVE2D_ENABLED = true" in html}')
    out()

    checks = []

    def chk(name, good, detail=''):
        checks.append((name, bool(good), detail))

    # ---- 1. 页面本体 ----
    st, hd, body = fetch(port, '/')
    ctype = (hd.get('Content-Type') or '').lower()
    chk('GET / 返回 200', st == 200, f'status={st}')
    chk('/ 是 HTML 且带 no-store', 'text/html' in ctype and hd.get('Cache-Control') == 'no-store',
        f'ctype={ctype} cache={hd.get("Cache-Control")}')
    chk('/ 的内容就是注入后的 ui.html', body.decode('utf-8', 'replace') == html,
        f'{len(body)} bytes')
    chk('Content-Length 与实体一致', hd.get('Content-Length') == str(len(body)),
        f'hdr={hd.get("Content-Length")} real={len(body)}')

    # ---- 2. favicon 走 204 ----
    st, hd, body = fetch(port, '/favicon.ico')
    chk('/favicon.ico 是 204 且无 body', st == 204 and body == b'',
        f'status={st} len={len(body)}')
    chk('204 不带 Content-Length（HTTP/1.1 规范）',
        'Content-Length' not in hd, f'hdr={hd.get("Content-Length")}')

    # ---- 3. vendor 两个 js ----
    for name, expect_path in (
        ('live2d-vendor.js', live2d_assets.VENDOR_JS),
        ('live2dcubismcore.min.js', live2d_assets.CORE_PATH),
    ):
        st, hd, body = fetch(port, '/vendor/' + name)
        size = expect_path.stat().st_size if expect_path.is_file() else -1
        chk(f'/vendor/{name} 200 且长度一致',
            st == 200 and len(body) == size, f'status={st} {len(body)} vs disk {size}')
        chk(f'/vendor/{name} 是 JS 类型',
            'javascript' in (hd.get('Content-Type') or '').lower(),
            hd.get('Content-Type'))

    # ---- 4. 整个模型闭包必须全 200 ----
    entry_rel = live2d_assets.MODEL_ENTRY.relative_to(live2d_assets.MODEL_ROOT).as_posix()
    st, hd, body = fetch(port, '/pet-assets/' + entry_rel)
    chk(f'/pet-assets/{entry_rel} 200', st == 200, f'status={st}')

    data = json.loads(body.decode('utf-8'))
    refs = live2d_assets._closure_refs(data)
    out(f'  model3.json 引用闭包: {len(refs)} 个文件')
    out(f'  Model Version = {data.get("Version")}')

    miss = []
    for ref in refs:
        url = '/pet-assets/' + live2d_assets.MODEL_ENTRY.parent.relative_to(
            live2d_assets.MODEL_ROOT).as_posix() + '/' + ref
        st, hd, blob = fetch(port, url)
        if st != 200 or not blob:
            miss.append(f'{ref} -> {st} ({len(blob)}B)')
    chk(f'闭包 {len(refs)} 个文件全部 200 且有内容', not miss,
        'all served' if not miss else f'缺 {len(miss)}: {miss[:4]}')

    # ---- 5. 路径越界必须挡住 ----
    escapes = [
        '/pet-assets/../../common.py',
        '/pet-assets/../../../desktop_pet/common.py',
        '/pet-assets/%2e%2e/%2e%2e/common.py',
        '/pet-assets/..%2f..%2fcommon.py',
        '/pet-assets/....//....//common.py',
        '/pet-assets/ds-whale-girl/../../../../common.py',
        '/vendor/../../common.py',
        '/vendor/%2e%2e/%2e%2e/desktop_pet/live2d_assets.py',
    ]
    bad = []
    for p in escapes:
        st, hd, blob = fetch(port, p)
        if st == 200 or b'def ' in blob or b'import ' in blob:
            bad.append(f'{p} -> {st} ({len(blob)}B)')
    chk(f'{len(escapes)} 条越界路径全被挡（非 200 / 无源码内容）', not bad,
        'all blocked' if not bad else f'泄漏: {bad}')

    # ---- 6. 未知路径 ----
    st, hd, body = fetch(port, '/nope.txt')
    chk('未知路径返回 404', st == 404, f'status={st}')

    # ---- 报告 ----
    out()
    out('  --- 断言 ---')
    for name, good, detail in checks:
        out(f"    [{'PASS' if good else 'FAIL'}] {name:<44} {detail}")

    all_ok = all(g for _, g, _ in checks)
    out()
    out(f'  通过 {sum(1 for _, g, _ in checks if g)}/{len(checks)}')
    out('[PASS] 生产静态服务器可用。' if all_ok else '[FAIL] 生产静态服务器有问题。')
    return 0 if all_ok else 1


if __name__ == '__main__':
    try:
        code = main()
    except Exception:
        import traceback
        out(traceback.format_exc())
        code = 3
    sys.exit(code)
