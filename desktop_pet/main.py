"""
main.py - Airi 桌面宠物入口（pywebview 窗口）

通过 SSE 连接到 Airi 桌宠服务器（standalone.py，v0.2 起扩展内不再内置
HTTP 服务器），接收消息并显示为可拖动的透明无边框桌面窗口。

【运行方式】
  python main.py --port <PORT>

  PORT: Airi 桌宠服务器端口（standalone.py，默认 19876）

【依赖】
  pip install pywebview
"""

import sys
import argparse

from common import get_screen_size, WindowAPI, load_html, create_temp_html


def main():
    parser = argparse.ArgumentParser(description='Airi Desktop Pet')
    parser.add_argument('--port', type=int, required=True,
                        help='VS Code extension HTTP server port')
    args = parser.parse_args()
    port = args.port

    try:
        import webview
    except ImportError:
        print("[airi-desktop] pywebview not installed. Run: pip install pywebview")
        sys.exit(1)

    html = load_html(port)
    html_url = create_temp_html(html, 'airi_pet_ui.html')

    screen_w, screen_h = get_screen_size()

    win_w, win_h = 300, 450
    x = screen_w - win_w - 40
    y = screen_h - win_h - 120

    api = WindowAPI()

    window = webview.create_window(
        title='Airi',
        url=html_url,
        width=win_w,
        height=win_h,
        x=x,
        y=y,
        frameless=True,
        transparent=True,
        on_top=True,
        resizable=False,
        easy_drag=False,
        js_api=api,
    )

    api.set_window(window)

    webview.start(debug=False)


if __name__ == '__main__':
    main()
