"""test_push_e2e.py — /push 两路行为端到端测试（真 socket，离线可跑）。

场景 A：_backend_available=False -> _builtin_reply 兜底台词（v0.3.10 修复）。
场景 B：真实后端状态 -> generate_response 产出非空气泡。
用法: python test_push_e2e.py  （结果写 live2d_probe/_e2e_test.txt）
"""
import io
import json
import os
import sys
import threading
import time
import urllib.request

ROOT = r"S:\My event\projects\vscode-anime-assistent\desktop_pet"
sys.path.insert(0, ROOT)
lines = []

import standalone

PORT = 29876
ready = threading.Event()
t = threading.Thread(target=standalone.start_server, args=(PORT, ready), daemon=True)
t.start()
ok = ready.wait(timeout=5.0)
lines.append('server ready: %s' % ok)

if ok:
    # monkeypatch push_message 以捕获真实 do_POST 的调用
    captured = []
    standalone.push_message = lambda typ, txt, emo='idle': captured.append((typ, txt, emo))
    orig_backend = standalone._backend_available

    def post(path, body):
        req = urllib.request.Request(
            'http://127.0.0.1:%d%s' % (PORT, path),
            data=json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')

    # 场景 A：强制后端不可用 -> 必须走 _builtin_reply 兜底（本轮修的 bug）
    standalone._backend_available = False
    captured.clear()
    code, body = post('/push', {'trigger': 'all_clear', 'error_count': 0,
                                'language': 'unknown', 'files': [], 'sample_errors': []})
    good = (code == 200 and captured and captured[-1] ==
            ('chatMessage', '哼，全部修好了…算你厉害。', 'happy'))
    ok = ok and good
    lines.append('A1 all_clear(强制无后端) -> http=%d captured=%s %s'
                 % (code, captured[-1] if captured else None, 'OK' if good else 'FAIL'))

    captured.clear()
    code, body = post('/push', {'type': 'diagnostics',
                                'payload': {'count': 1, 'items': [], 'language': 'python'}})
    good = (code == 200 and captured and captured[-1] ==
            ('errorAlert', '喂！1 个错误！给我认真点检查！', 'angry'))
    ok = ok and good
    lines.append('A2 diagnostics(强制无后端) -> http=%d captured=%s %s'
                 % (code, captured[-1] if captured else None, 'OK' if good else 'FAIL'))

    # 场景 B：恢复真实后端状态 -> 两种形状都必须产出**非空**气泡回复
    standalone._backend_available = orig_backend
    captured.clear()
    code, body = post('/push', {'trigger': 'all_clear', 'error_count': 0,
                                'language': 'unknown', 'files': [], 'sample_errors': []})
    good = (code == 200 and captured and captured[-1][1] != '')
    ok = ok and good
    lines.append('B1 all_clear(真实后端) -> http=%d captured=%s %s'
                 % (code, captured[-1] if captured else None, 'OK' if good else 'FAIL'))

    captured.clear()
    code, body = post('/push', {'type': 'diagnostics',
                                'payload': {'count': 1, 'items': [], 'language': 'python'}})
    good = (code == 200 and captured and captured[-1][1] != '')
    ok = ok and good
    lines.append('B2 diagnostics(真实后端) -> http=%d captured=%s %s'
                 % (code, captured[-1] if captured else None, 'OK' if good else 'FAIL'))

lines.append('OVERALL %s' % ('PASS' if ok else 'FAIL'))
with io.open(os.path.join(ROOT, '_e2e_test.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')
print('done')
