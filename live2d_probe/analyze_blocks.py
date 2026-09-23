"""_analyze_blocks.py — 在用户截图里把「白色/浅色方块」逐个量出来。

为什么不用眼睛看
----------------
上一轮我就是靠"看截图"把模型自带的女仆头饰当成结论，结果被打回。
这次一律走像素：找出窗口矩形 → 在窗口内按颜色分类 → 连通块 → 每个块给
bbox（窗口相对坐标）+ 面积 + 平均色 + 矩形度(area/bbox)。

矩形度很重要：**矩形度接近 1 的浅色块 = 某个 DOM 元素画的背景**，
而模型美术（头饰、桌板）的矩形度会明显小于 1。

产物：_blocks.txt（表格 + ASCII 图）、_blocks_annot.png（画框标注）
"""
import sys
from collections import deque
from pathlib import Path

from PIL import Image

SRC = Path(sys.argv[1])
OUT_TXT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('_blocks.txt')
OUT_PNG = Path(sys.argv[3]) if len(sys.argv) > 3 else Path('_blocks_annot.png')

img = Image.open(SRC).convert('RGB')
W, H = img.size
px = img.load()

L = []


def out(s=''):
    L.append(s)


# ---------- 1) 定位窗口：用户这次是深色模式，slab ≈ #202020 ----------
cand = []
for y in range(H):
    for x in range(W):
        r, g, b = px[x, y]
        if abs(r - 32) <= 5 and abs(g - 32) <= 5 and abs(b - 32) <= 5:
            cand.append((x, y))
if not cand:
    out('没找到 #202020 的 slab —— 换一个判据（找最大同色矩形）')
    raise SystemExit(2)
wx0 = min(c[0] for c in cand)
wx1 = max(c[0] for c in cand) + 1
wy0 = min(c[1] for c in cand)
wy1 = max(c[1] for c in cand) + 1
out('图片尺寸            : %dx%d' % (W, H))
out('窗口（含 slab）bbox : (%d,%d)-(%d,%d)  = %dx%d'
    % (wx0, wy0, wx1, wy1, wx1 - wx0, wy1 - wy0))
out('期望 client 284x441（上一轮日志），差一点是边缘抗锯齿/估算所致')
out()

# ---------- 2) 窗口内分类 ----------
CW, CH = wx1 - wx0, wy1 - wy0
kind = [[' '] * CW for _ in range(CH)]
for y in range(CH):
    for x in range(CW):
        r, g, b = px[wx0 + x, wy0 + y]
        mn, mx = min(r, g, b), max(r, g, b)
        lum = (r * 299 + g * 587 + b * 114) // 1000
        if mn >= 215:
            kind[y][x] = 'W'          # 近白
        elif lum >= 95 and (mx - mn) <= 45:
            kind[y][x] = 'g'          # 浅灰（低饱和）
        else:
            kind[y][x] = '.'          # 其他（深色/彩色 = 角色美术）

# ---------- 3) 连通块 ----------
def components(ch):
    seen = [[False] * CW for _ in range(CH)]
    res = []
    for y0 in range(CH):
        for x0 in range(CW):
            if seen[y0][x0] or kind[y0][x0] != ch:
                continue
            q = deque([(x0, y0)])
            seen[y0][x0] = True
            pts = []
            while q:
                x, y = q.popleft()
                pts.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < CW and 0 <= ny < CH and not seen[ny][nx] \
                            and kind[ny][nx] == ch:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bx0, bx1, by0, by1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
            area = len(pts)
            bbox = (bx1 - bx0) * (by1 - by0)
            cols = [px[wx0 + p[0], wy0 + p[1]] for p in pts]
            mean = tuple(sum(c[i] for c in cols) // len(cols) for i in range(3))
            res.append({
                'ch': ch, 'area': area,
                'x0': bx0, 'y0': by0, 'x1': bx1, 'y1': by1,
                'fill': area / float(bbox), 'mean': mean,
                'touch': (bx0 == 0 or by0 == 0 or bx1 == CW or by1 == CH),
            })
    return sorted(res, key=lambda r: -r['area'])


rows = []
for ch in ('W', 'g'):
    for c in components(ch):
        if c['area'] >= 25:
            rows.append(c)

out('窗口内浅色连通块（面积≥25px，按面积降序）')
out('字符 : W=近白(所有通道≥215)  g=浅灰(亮度≥95 且 通道差≤45)  坐标=窗口相对像素')
out('-' * 100)
out('%-3s %6s %-24s %8s %12s %-16s %s'
    % ('色', '面积', 'bbox(x0,y0,x1,y1)', '占窗%', '矩形度', '平均色', '占宽/占高'))
out('-' * 100)
for c in rows:
    bw, bh = c['x1'] - c['x0'], c['y1'] - c['y0']
    out('%-3s %6d (%4d,%4d,%4d,%4d) %7.2f%% %12.3f rgb%-14s %.0f%%x%.0f%%%s'
        % (c['ch'], c['area'], c['x0'], c['y0'], c['x1'], c['y1'],
           100.0 * c['area'] / (CW * CH), c['fill'], str(c['mean']),
           100.0 * bw / CW, 100.0 * bh / CH,
           '  [贴边]' if c['touch'] else ''))
out()
out('窗口尺寸：%d x %d' % (CW, CH))

# ---------- 4) ASCII 图（每 4x4 取样，取出现最多的类别）----------
out()
out('窗口内 ASCII 图（每格 4x4 像素；W=近白 g=浅灰 .=其他/深色）')
out('      ' + ''.join(str((i // 10) % 10) if i % 10 == 0 else ' '
                       for i in range(0, CW, 4)))
for y in range(0, CH, 4):
    row = []
    for x in range(0, CW, 4):
        tally = {}
        for yy in range(y, min(y + 4, CH)):
            for xx in range(x, min(x + 4, CW)):
                k = kind[yy][xx]
                tally[k] = tally.get(k, 0) + 1
        row.append(max(tally.items(), key=lambda kv: kv[1])[0])
    out('%4d  %s' % (y, ''.join(row)))

OUT_TXT.write_text('\n'.join(L) + '\n', encoding='utf-8')

# ---------- 5) 标注图 ----------
ann = img.copy()
d = ann.load()
for c in rows:
    x0, y0, x1, y1 = (wx0 + c['x0'], wy0 + c['y0'], wx0 + c['x1'], wy0 + c['y1'])
    col = (255, 0, 0) if c['ch'] == 'W' else (0, 160, 255)
    for x in range(max(0, x0 - 1), min(W, x1 + 1)):
        for yy in (y0 - 1, y0 - 2, y1, y1 + 1):
            if 0 <= yy < H:
                d[x, yy] = col
    for y in range(max(0, y0 - 1), min(H, y1 + 1)):
        for xx in (x0 - 1, x0 - 2, x1, x1 + 1):
            if 0 <= xx < W:
                d[xx, y] = col
ann = ann.resize((W * 2, H * 2), Image.NEAREST)
ann.save(OUT_PNG)
print(OUT_TXT, OUT_PNG, ann.size)
