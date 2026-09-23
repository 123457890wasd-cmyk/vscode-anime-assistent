# -*- coding: utf-8 -*-
"""最小 pywebview 桥测试：验证探针 venv 里 JS->pywebview->Python 到底通不通。

页面极简（无 ui.html 的任何逻辑），如果这里桥都不通，说明 venv 的
pywebview/WebView2 环境本身有独立问题，跟项目代码无关。
"""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'bridge_test.txt')

RECV = []
PORT = 23456


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith('/note'):
            RECV.append('GET %s' % self.path)
            self.send_response(204)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        page = ('<!doctype html><meta charset="utf-8"><html><body><script>\n'
                'function post(t){fetch("/note?t="+encodeURIComponent(t)).catch(function(e){});}\n'
                'function tryPing(tag){\n'
                '  try{\n'
                '    var w=window.pywebview;\n'
                '    if(!w){post(tag+" nowebview");return;}\n'
                '    var a=w.api;\n'
                '    if(!a){post(tag+" noapi");return;}\n'
                '    if(!a.ping){post(tag+" nopings keys="+Object.keys(a).join(","));return;}\n'
                '    Promise.resolve(a.ping()).then(function(r){post(tag+" OK "+JSON.stringify(r));},\n'
                '                                      function(e){post(tag+" REJ "+e);});\n'
                '  }catch(e){post(tag+" EXC "+e);}\n'
                '}\n'
                'tryPing("load");\n'
                'window.addEventListener("pywebviewready",function(){tryPing("evt");});\n'
                'setTimeout(function(){tryPing("t3");},3000);\n'
                'setTimeout(function(){tryPing("t8");},8000);\n'
                '</script></body></html>').encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        RECV.append('POST %s' % self.path)
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'ok')


def main():
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    import webview
    L = ['pywebview version: %s' % getattr(webview, '__version__', '?')]

    class Api:
        def ping(self):
            return {'ok': True}

    api = Api()
    webview.create_window('bridgetest', 'http://127.0.0.1:%d/' % PORT,
                          js_api=api, width=300, height=200,
                          frameless=True, transparent=True, on_top=True,
                          easy_drag=False, resizable=False)

    def _killer():
        time.sleep(10)
        try:
            for w in webview.windows:
                w.destroy()
        except Exception:
            pass

    threading.Thread(target=_killer, daemon=True).start()
    webview.start()
    L.append('posts received:')
    L.extend('  ' + r for r in RECV) or L.append('  (none)')

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


if __name__ == '__main__':
    try:
        main()
    finally:
        with open(OUT, 'a', encoding='utf-8') as f:
            f.write('posts received: %d\n' % len(RECV))
            for r in RECV:
                f.write('  %s\n' % r)
            f.write('END\n')
