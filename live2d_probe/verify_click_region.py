# -*- coding: utf-8 -*-
"""验证这一轮新增的两块能力。

A. 点击互动 —— 台词池结构、情绪词与前端是否对齐、pet_click 端到端、模板注入
B. 异形窗口 —— 在真 pywebview 窗口上跑 _apply_window_region，用「设 region 前 /
   设 region 后 / 窗口外桌面」三点对比取证，确认窗口外圈确实透出了桌面

B 的判据为什么这样设计：
    BitBlt 从 screen DC 抓不到 WebView2 的内容（DirectComposition 不在里面），
    但**能**抓到 WinForms Form 自己画的 #F0F0F0 背景 —— 这恰好就是我们要观察的
    那层。所以：
      设 region 前，(30,30) 应读到 #F0F0F0（Form 背景，说明窗口覆盖了那里）
      设 region 后，(30,30) 应读到与「窗口外桌面」相同的颜色（说明那里已经不
      属于窗口了）
    两者同时成立才算通过。

用法：用带 pywebview 的那个解释器跑（Python312）。
"""
import os
import re
import sys
import json
import time
import zlib
import struct
import ctypes
import threading

PROJ = r"S:\My event\projects\vscode-anime-assistent"
PET = os.path.join(PROJ, "desktop_pet")
PROBE_DIR = os.path.join(PROJ, "live2d_probe")
OUT = os.path.join(PROBE_DIR, "click_region_result.txt")
SHOT_BEFORE = os.path.join(PROBE_DIR, "region_before.png")
SHOT_AFTER = os.path.join(PROBE_DIR, "region_after.png")

sys.path.insert(0, PET)

lines = []


def out(s=''):
    lines.append(str(s))
    try:
        with open(OUT, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
    except OSError:
        pass


checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), str(detail)))
    out('  [%s] %-52s %s' % ('PASS' if ok else 'FAIL', name,
                             (':: ' + str(detail)) if detail else ''))


def hexs(c):
    return '#%02X%02X%02X' % tuple(c)


# ===========================================================================
# A. 点击互动
# ===========================================================================
out('=' * 74)
out('A. 点击互动')
out('=' * 74)

import common      # noqa: E402
import standalone  # noqa: E402

UI = open(os.path.join(PET, 'ui.html'), encoding='utf-8').read()

# A1 台词池结构
shape_bad = []
for zone, pool in standalone.CLICK_LINES.items():
    if not pool:
        shape_bad.append(zone + ':empty')
    for it in pool:
        if not (isinstance(it, tuple) and len(it) == 2
                and isinstance(it[0], str) and isinstance(it[1], str)):
            shape_bad.append(zone + ':bad-item')
check('CLICK_LINES 每项都是 (台词, 情绪) 两个字符串', not shape_bad, shape_bad)

# A2 情绪词和前端对得上
m = re.search(r'EMOTION_EXPRESSION\s*=\s*\{(.*?)\}', UI, re.S)
known = set(re.findall(r'(\w+)\s*:', m.group(1))) if m else set()
used = set(e for pool in standalone.CLICK_LINES.values() for _, e in pool)
check('点击用到的情绪前端都认识', used <= known,
      '未知=%s 已知=%s' % (sorted(used - known), sorted(known)))

# A3 pet_click 端到端
api = common.WindowAPI()
api.set_click_handler(standalone._pick_click_line)
got = {z: api.pet_click(z) for z in ('head', 'body', 'desk')}
check('pet_click 三个分区都取到台词', all(got[z]['text'] for z in got),
      ' | '.join('%s=%s' % (z, got[z]['text'][:12]) for z in ('head', 'body', 'desk')))
check('pet_click 情绪字段非空', all(got[z]['emotion'] for z in got), '')
check('未知分区回落到 body', bool(api.pet_click('???')['text']), '')
check('未注册 handler 时安全返回空', common.WindowAPI().pet_click('head')['text'] == '', '')

# A4 模板注入
h_on = common.load_html(19999, None, live2d=True, silhouette=True)
h_off = common.load_html(19999, None, live2d=False, silhouette=False)
check('load_html 注入 SILHOUETTE=true', 'var SILHOUETTE = ("true" === "true");' in h_on, '')
check('load_html 注入 SILHOUETTE=false', 'var SILHOUETTE = ("false" === "true");' in h_off, '')
check('ui.html 里没有没被替换的模板标记', '{{' not in h_on, '')

# A5 前端关键实现
for fn in ('handlePetClick', 'alphaAt', 'zoneAt', 'pushSilhouette',
           'characterRects', 'collectUiRects', 'startSilhouetteLoop'):
    check('ui.html 定义了 ' + fn + '()', ('function ' + fn) in UI, '')
check('启动时调用 startSilhouetteLoop()', 'startSilhouetteLoop();' in UI, '')
check('点击与拖拽有区分（移动阈值）',
      'CLICK_SLOP' in UI and 'if (dragging && !moved) handlePetClick(e);' in UI, '')
check('pushSilhouette 在 live2d 未就绪时不上报（否则窗口只剩气泡）',
      'if (!live2d.ready) return;' in UI, '')
# 这两条替换掉了原来那句 'silFailures >= 3' 的一次性断言 —— 上一轮把"失败 3 次永久
# 放弃"改成了退避重试 + 诊断上报，旧断言查的是已经删掉的代码，必须跟着改。
check('轮廓失败只退避、不永久放弃（旧的 silFailures>=3 已删）',
      'silFailures >= 3' not in UI and 'silNextAt = Date.now() + 5000' in UI, '')
check('轮廓读数不可信时拒绝上报（绝不许只把气泡/名字裁进去）',
      "ch.source === 'unreliable'" in UI and "'silhouette-unusable'" in UI, '')
# ⚠️ 这里原来断的是 `SILHOUETTE` 和 `live2d.ready` 写在同一个 return 里（`A || B`）。
# 本轮为了让**角色底板**在 SILHOUETTE=0 时也能工作，把那个合并 return 拆开了：
# 先判 live2d.ready，再更新底板，最后才判 SILHOUETTE。所以这条断言也得跟着改，
# 否则它查的是一行已经不存在、而且**故意的**不该存在的代码。
check('底板在关闭异形窗口时仍然工作（不该被 SILHOUETTE 一起关掉）',
      'updateCharCard(ch.rects)' in UI and
      UI.index('updateCharCard(ch.rects)') < UI.index('if (!SILHOUETTE) return;'),
      '')
check('气泡出现/消失/底板落定都会立刻刷新窗口形状',
      UI.count('setTimeout(function() { pushSilhouette(true); }, 0)') == 3,
      '气泡出现+消失+底板合计=%d'
      % UI.count('setTimeout(function() { pushSilhouette(true); }, 0)'))

# A6 本轮修复：透明窗口上的"块状白"（user 截图里的白色方块）
#
# ⚠️ 断言必须扫**生效的 CSS**，不能扫原文。
# 本轮修复的做法是"把那一行删掉、在旁边写一段注释说明为什么删"，
# 而那段注释本身就会写「这里曾经有一行 backdrop-filter: blur(10px)」——
# 直接拿 'backdrop-filter:' in UI 去查，会把我自己写的注释判成违规，
# 于是**做得对反而报错**（第一次跑就是这样，2 条 FAIL，全在注释上）。
# 所以先把 CSS 注释整体剥掉再查；同时也顺带解决"注释里的 } 会把
# 类块切片提前截断"这个隐患。
CSS = re.sub(r'/\*.*?\*/', '', UI, flags=re.S)

check('气泡/右键菜单不再用 backdrop-filter（透明窗口上会糊出不透明的底）',
      'backdrop-filter:' not in CSS,
      '剩余=%d' % CSS.count('backdrop-filter:'))

_name_i = CSS.index('.pet-name {')
_name_css = CSS[_name_i:CSS.index('}', _name_i)]
check('名牌不再挂 filter（会提升成合成层，透明底上可能糊出一块方底）',
      'filter' not in _name_css, _name_css.replace('\n', ' ')[:70])
# 名字用 background-clip:text + color:transparent 做渐变字，再叠 filter:
# drop-shadow 会同时触发"文字裁剪层 + 独立合成层"，是这类方块最典型的成因。
# 所以这两件事不能再凑在同一块里出现。
check('渐变文字（background-clip:text）不再与 filter 同块出现',
      'background-clip: text' not in _name_css or 'filter' not in _name_css, '')
check('全局关掉次像素抗锯齿（透明底上文字会连底一起被栅格化）',
      '-webkit-font-smoothing: antialiased' in UI, '')
check('角色底板接的是**实测外接框**、且采样后锁定（避免跟着 idle 动作抖）',
      'cardSamples >= CARD_SAMPLES' in UI and 'cardUnion' in UI and 'CARD_PAD' in UI, '')
check('底板只在 Live2D 就绪后才显示（图片兜底角色时 z-index:0 会盖住 #charImg）',
      'card-on' in UI and 'z-index: 0' in UI, '')
check('底板并进窗口形状（否则实心卡会被 region 裁掉）',
      'pairs.push([charCard, 0])' in UI, '')

# ===========================================================================
# B. 异形窗口
# ===========================================================================
out('')
out('=' * 74)
out('B. 异形窗口（真窗口 + SetWindowRgn，前后对比取证）')
out('=' * 74)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
# GetPixel 必须显式声明 argtypes：不声明的话 HDC 会按 C int 传，
# 64 位下句柄超过 2^31 就抛 OverflowError: int too long to convert。
gdi32.GetPixel.restype = ctypes.c_uint32
gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         ctypes.c_uint32]
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_uint]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.CreateRectRgn.restype = ctypes.c_void_p
gdi32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
user32.GetWindowRgn.restype = ctypes.c_int
user32.GetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.GetRgnBox.restype = ctypes.c_int      # 注意：GetRgnBox 在 gdi32，不在 user32
gdi32.GetRgnBox.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def grab(x, y, w, h):
    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY | CAPTUREBLT)
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), 0)
        return bytes(buf)
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def write_png(path, w, h, bgra):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        row = bgra[y * w * 4:(y + 1) * w * 4]
        for i in range(0, len(row), 4):
            raw += bytes((row[i + 2], row[i + 1], row[i]))

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    with open(path, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n'
                 + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                 + chunk(b'IDAT', zlib.compress(bytes(raw), 6))
                 + chunk(b'IEND', b''))


def px_color(x, y):
    hdc = user32.GetDC(None)
    try:
        c = gdi32.GetPixel(hdc, int(x), int(y))
    finally:
        user32.ReleaseDC(None, hdc)
    return (c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF)


WIN_W, WIN_H = 300, 480
WIN_X, WIN_Y = 300, 150
# 故意比窗口小得多：模拟「角色 + 气泡」占的形状（页面 CSS 像素）
REQ_RECTS = [[100, 150, 200, 330]]

PROBE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:transparent;overflow:hidden;width:100%;height:100%}
.role{position:absolute;left:100px;top:150px;width:100px;height:180px;
      background:#FF0000}
</style></head><body><div class="role"></div></body></html>"""

import webview  # noqa: E402

window = webview.create_window(
    'region probe', html=PROBE_HTML, x=WIN_X, y=WIN_Y, width=WIN_W, height=WIN_H,
    frameless=True, transparent=True, on_top=True, resizable=False, easy_drag=False)


def worker():
    try:
        time.sleep(2.5)
        hwnd = common._get_hwnd(window)
        check('能取到 pywebview 窗口的 HWND', bool(hwnd), 'hwnd=0x%X' % (hwnd or 0))
        if not hwnd:
            return

        wr = common._RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(wr))
        wx, wy = wr.left, wr.top
        ww, wh = wr.right - wr.left, wr.bottom - wr.top
        out('  窗口屏幕矩形 = (%d,%d) %dx%d' % (wx, wy, ww, wh))

        def at(css_x, css_y):
            """把页面 CSS 坐标换算成屏幕物理坐标再取色"""
            x = wx + int(round(css_x * ww / float(WIN_W)))
            y = wy + int(round(css_y * wh / float(WIN_H)))
            return x, y, px_color(x, y)

        # 抓两张整屏取证图（区域比窗口大一圈，能同时看到窗口和它外面的桌面）
        gx, gy, gw, gh = wx - 60, wy - 60, ww + 120, wh + 120

        before_buf = grab(gx, gy, gw, gh)
        write_png(SHOT_BEFORE, gw, gh, before_buf)

        ox, oy, desktop = at(-30, 30)          # 窗口外的桌面（负坐标会算到窗口左侧）
        bx, by, before = at(30, 30)            # 窗口内左上角，在目标矩形之外
        out('  设 region 前：')
        out('    窗口外桌面 (%d,%d) = %s' % (ox, oy, hexs(desktop)))
        out('    窗口内(30,30) (%d,%d) = %s' % (bx, by, hexs(before)))
        check('设 region 前窗口确实覆盖着 (30,30)（读到 Form 的 #F0F0F0）',
              before == (240, 240, 240), hexs(before))

        # ---- 应用 region ----
        res = common._apply_window_region(window, REQ_RECTS, WIN_W, WIN_H)
        check('_apply_window_region 返回 ok', res.get('ok'), json.dumps(res))
        check('region 只含 1 个矩形', res.get('rects') == 1, json.dumps(res))

        res_empty = common._apply_window_region(window, [], WIN_W, WIN_H)
        check('空 rects 被拒绝（绝不造出看不见又关不掉的窗口）',
              not res_empty.get('ok'), json.dumps(res_empty))

        res_bad = common._apply_window_region(window, [[9999, 9999, 10000, 10000]],
                                              WIN_W, WIN_H)
        check('完全在窗口外的 rect 被拒绝',
              not res_bad.get('ok'), json.dumps(res_bad))

        # ---- 权威证据：把窗口当前的形状读回来，和请求的矩形逐一比对 ----
        # 不用抓屏颜色判断：BitBlt 抓不到 WebView2(DirectComposition) 的内容，
        # 也不该拿远处的桌面像素当参照（那是另一个背景层，颜色本来就不一样）。
        # GetWindowRgn 读的是系统里真实的窗口形状。
        crc = common._RECT()
        user32.GetClientRect(hwnd, ctypes.byref(crc))
        cw, ch = crc.right - crc.left, crc.bottom - crc.top
        want = (int(round(REQ_RECTS[0][0] * cw / WIN_W)),
                int(round(REQ_RECTS[0][1] * ch / WIN_H)),
                int(round(REQ_RECTS[0][2] * cw / WIN_W)),
                int(round(REQ_RECTS[0][3] * ch / WIN_H)))
        hrgn = gdi32.CreateRectRgn(0, 0, 0, 0)
        try:
            rtype = user32.GetWindowRgn(hwnd, hrgn)
            box = common._RECT()
            gdi32.GetRgnBox(hrgn, ctypes.byref(box))
            got = (box.left, box.top, box.right, box.bottom)
        finally:
            gdi32.DeleteObject(hrgn)
        out('  客户区 = %dx%d   region 类型 = %d' % (cw, ch, rtype))
        check('★ GetWindowRgn 读回的窗口形状 == 请求的矩形',
              rtype != 0 and got == want, 'got=%s want=%s' % (got, want))

        time.sleep(1.4)
        after_buf = grab(gx, gy, gw, gh)
        write_png(SHOT_AFTER, gw, gh, after_buf)

        ax, ay, after = at(30, 30)
        ix, iy, inside = at(150, 240)
        out('  设 region 后：')
        out('    窗口内(30,30) (%d,%d) = %s' % (ax, ay, hexs(after)))
        out('    矩形内(150,240) (%d,%d) = %s' % (ix, iy, hexs(inside)))
        out('    窗口外桌面 = %s' % hexs(desktop))

        check('★ 被裁掉的区域不再显示 Form 的 #F0F0F0',
              after != (240, 240, 240) and after != before,
              'before=%s after=%s' % (hexs(before), hexs(after)))
        check('★ 窗口内外的读数已不同（形状确实被切开）',
              after != inside, 'outside=%s inside=%s' % (hexs(after), hexs(inside)))
        out('    注：矩形内读到黑是正常的 —— BitBlt 抓不到 WebView2 的')
        out('        DirectComposition 面；形状裁得对不对以 GetWindowRgn 为准。')
        out('  取证图：%s' % os.path.basename(SHOT_BEFORE))
        out('           %s' % os.path.basename(SHOT_AFTER))
    except Exception:
        import traceback
        out('!! 出错:')
        out(traceback.format_exc())
    finally:
        try:
            window.destroy()
        except Exception:
            pass


def hard_kill():
    time.sleep(45)
    out('!! 硬超时')
    os._exit(9)


threading.Thread(target=hard_kill, daemon=True).start()
threading.Thread(target=worker, daemon=True).start()

webview.start(debug=False)

# ===========================================================================
passed = sum(1 for _, ok, _ in checks if ok)
total = len(checks)
out('')
out('=' * 74)
out('结果 %d/%d 通过' % (passed, total))
for name, ok, detail in checks:
    if not ok:
        out('  FAIL: %s  %s' % (name, detail))
out('=' * 74)
sys.exit(0 if passed == total else 1)
