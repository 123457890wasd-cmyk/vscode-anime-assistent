"""_sample_region.py — 打印截图里某区域的精确颜色分布 + 行程编码（看边缘在哪）。

用法：python _sample_region.py <src.png> <窗口绝对 wx0,wy0> <区域窗口相对 rx0,ry0,rx1,ry1> <out.txt>
"""
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

src = Path(sys.argv[1])
wx0, wy0 = (int(v) for v in sys.argv[2].split(',')[:2])
rx0, ry0, rx1, ry1 = (int(v) for v in sys.argv[3].split(','))

img = Image.open(src).convert('RGB')
px = img.load()
L = []
W, H = rx1 - rx0, ry1 - ry0


def rgb(x, y):
    return px[wx0 + x, wy0 + y]


def rle(seq, base, axis):
    parts = []
    cur = seq[0]
    start = 0
    for i in range(1, len(seq)):
        if seq[i] != cur:
            parts.append('%s%d-%d rgb%s' % (axis, base + start, base + i - 1, str(cur)))
            cur, start = seq[i], i
    parts.append('%s%d-%d rgb%s' % (axis, base + start, base + len(seq) - 1, str(cur)))
    return parts


c = Counter(rgb(x, y) for y in range(ry0, ry1) for x in range(rx0, rx1))
L.append('区域（窗口相对）: (%d,%d)-(%d,%d)  %dx%d' % (rx0, ry0, rx1, ry1, W, H))
L.append('Top 10 颜色：')
for col, n in c.most_common(10):
    L.append('   rgb%-16s %6d  %.1f%%' % (str(col), n, 100.0 * n / (W * H)))
L.append('')
L.append('横向行程（看左右边）')
for y in range(ry0, ry1, 3):
    for p in rle([rgb(x, y) for x in range(rx0, rx1)], rx0, 'x'):
        L.append('  y=%3d  %s' % (y, p))
L.append('')
L.append('纵向行程（看上下边）')
for x in range(rx0, rx1, 6):
    for p in rle([rgb(x, y) for y in range(ry0, ry1)], ry0, 'y'):
        L.append('  x=%3d  %s' % (x, p))

Path(sys.argv[4]).write_text('\n'.join(L) + '\n', encoding='utf-8')
print(sys.argv[4])
