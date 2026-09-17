"""
common.py — Airi 桌宠共用工具

被 standalone.py / main.py 引用，提供：
  - get_screen_size():        主屏幕分辨率
  - WindowAPI:                暴露给 ui.html (window.pywebview.api) 的窗口控制
  - find_character_image():   查找自定义立绘 assets/character.png
  - load_html(port, char_img):读取 ui.html 并注入 {{PORT}} / {{CHARACTER_IMAGE}}
  - create_temp_html():       写临时 HTML 并返回 file:// URL
"""

import ctypes
import os
import sys
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# 屏幕
# ---------------------------------------------------------------------------

def get_screen_size():
    """返回主屏幕分辨率 (width, height)。"""
    if sys.platform == 'win32':
        try:
            # 进程需要 DPI aware 才能拿到物理像素，否则 GetSystemMetrics
            # 返回缩放后的逻辑分辨率，导致窗口定位偏移
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            pass
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        size = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
        return size
    except Exception:
        return (1920, 1080)


# ---------------------------------------------------------------------------
# 窗口控制（pywebview js_api）
# ---------------------------------------------------------------------------

class WindowAPI:
    """ui.html 通过 window.pywebview.api 调用：拖拽移动 / 读取位置 / 隐藏。"""

    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def get_position(self):
        if self._window is None:
            return [0, 0]
        return [self._window.x or 0, self._window.y or 0]

    def move_window(self, x, y):
        if self._window is not None:
            try:
                self._window.move(int(x), int(y))
            except Exception:
                pass

    def exit_app(self):
        """销毁窗口并退出进程（webview.start 返回后主线程结束，daemon 线程随之退出）"""
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 立绘与 HTML
# ---------------------------------------------------------------------------

def find_character_image():
    """查找自定义立绘，找不到返回 None（ui.html 会退回 CSS 角色）。

    优先级：AIRI_CHARACTER_IMAGE 环境变量 > assets/character.* > 根目录 character.png
    """
    env_path = os.environ.get('AIRI_CHARACTER_IMAGE', '').strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)
        print(f'[airi-common] WARNING: AIRI_CHARACTER_IMAGE not found: {env_path}')
    for name in ('character.png', 'character.gif', 'character.webp'):
        p = _SCRIPT_DIR / 'assets' / name
        if p.is_file():
            return str(p)
    p = _SCRIPT_DIR / 'character.png'
    return str(p) if p.is_file() else None


def load_html(port, char_img=None):
    """读取 ui.html，注入端口和立绘 file:// URI，返回最终 HTML 字符串。"""
    html = (_SCRIPT_DIR / 'ui.html').read_text(encoding='utf-8')
    img_uri = Path(char_img).as_uri() if char_img else ''
    html = html.replace('{{PORT}}', str(int(port)))
    html = html.replace('{{CHARACTER_IMAGE}}', img_uri)
    return html


def create_temp_html(html, name='airi_ui.html'):
    """把 HTML 写入系统临时目录，返回可直接交给 pywebview 的 file:// URL。"""
    path = Path(tempfile.gettempdir()) / name
    path.write_text(html, encoding='utf-8')
    return path.as_uri()
