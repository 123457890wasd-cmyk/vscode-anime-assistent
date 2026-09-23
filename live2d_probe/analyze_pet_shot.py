# -*- coding: utf-8 -*-
"""分析用户截图里「黑底 + 角色周围块状白色」的真实几何。

要回答的问题（顺序就是排查顺序）：
  1. 那块黑是不是**整个窗口矩形**？—— 是的话说明 SetWindowRgn 根本没裁下去
     （页面报上来的轮廓覆盖了整张画布），不是"黑色背景"的问题。
  2. 白色块的**包围盒是否有规律**？—— 规整矩形 ⇒ 是轮廓/元素矩形；
     不规则 ⇒ 是模型自身贴图。
  3. 黑 === 窗口矩形 且 区域没裁 ⇒ 真正要做的是"让轮廓别覆盖整张画布"。

输出 ASCII 结构图 + 黑矩形包围盒 + 白连通块清单 + 颜色直方图。
"""
import os
import sys
from collections import Counter
from PIL import Image

SRC = (sys.argv[1] if len(sys.argv) > 1 else
       r"C:\Users\Mr.hancard\.workbuddy\clipboard-images"
       r"\clipboard-2026-09-22T14-40-44-185Z-05c95d55.png")
OUT = os.path.join(
    r"S:\My event\projects\vscode-anime-assistent\live2d_probe",
    "shot_pixels_%s.txt" % os.path.splitext(os.path.basename(SRC))[0][-8:])

img = Image.open(SRC).convert("RGB")
W, H = img.size
px = img.load()
lines = []
lines.append("image = %d x %d" % (W, H))

def isdark(c):
    return c[0] < 45 and c[1] < 45 and c[2] < 45

def iswhite(c):
    return c[0] > 232 and c[1] > 232 and c[2] > 232

# ---------- 1) 黑矩形包围盒 ----------
step = 3
row_hits = []
for y in range(H):
    n = sum(1 for x in range(0, W, step) if isdark(px[x, y]))
    row_hits.append(n)
col_hits = []
for x in range(W):
    n = sum(1 for y in range(0, H, step) if isdark(px[x, y]))
    col_hits.append(n)

nrow = len(range(0, W, step))
ncol = len(range(0, H, step))
dark_rows = [y for y, n in enumerate(row_hits) if n > nrow * 0.55]
dark_cols = [x for x, n in enumerate(col_hits) if n > ncol * 0.55]

lines.append("")
lines.append("=== 1) 黑矩形包围盒（行/列黑像素占比 > 55%）===")
if dark_rows and dark_cols:
    x0, x1 = dark_cols[0], dark_cols[-1]
    y0, y1 = dark_rows[0], dark_rows[-1]
    lines.append("  黑矩形 = x %d..%d  y %d..%d   尺寸 %d x %d" % (x0, x1, y0, y1, x1 - x0 + 1, y1 - y0 + 1))
    lines.append("  宽高比 = %.3f" % ((x1 - x0 + 1) / float(y1 - y0 + 1)))
    lines.append("  占整图面积 = %.1f%%" % ((x1 - x0 + 1) * (y1 - y0 + 1) / float(W * H) * 100))
    lines.append("  → 这就是窗口矩形。对比桌宠窗口宽高比可判断「region 是否等于整窗」。")
else:
    x0 = y0 = x1 = y1 = None
    lines.append("  没找到成规模的黑矩形（dark_rows=%d dark_cols=%d）" % (len(dark_rows), len(dark_cols)))

# ---------- 2) ASCII 结构图 ----------
lines.append("")
lines.append("=== 2) 结构图（每格 %dpx：K=黑 W=白 .=其它）===" % 10)
for y in range(0, H, 10):
    row = []
    for x in range(0, W, 10):
        c = px[x, y]
        row.append("K" if isdark(c) else ("W" if iswhite(c) else "."))
    lines.append("  %3d %s" % (y, "".join(row)))

# ---------- 3) 白色连通块 ----------
lines.append("")
lines.append("=== 3) 白色连通块（>=120 px）===")
seen = bytearray(W * H)
blocks = []
for sy in range(H):
    for sx in range(W):
        i = sy * W + sx
        if seen[i]:
            continue
        if not iswhite(px[sx, sy]):
            seen[i] = 1
            continue
        stack = [(sx, sy)]
        seen[i] = 1
        n = 0
        bx0 = bx1 = sx
        by0 = by1 = sy
        while stack:
            cx, cy = stack.pop()
            n += 1
            if cx < bx0: bx0 = cx
            if cx > bx1: bx1 = cx
            if cy < by0: by0 = cy
            if cy > by1: by1 = cy
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < W and 0 <= ny < H:
                    j = ny * W + nx
                    if not seen[j] and iswhite(px[nx, ny]):
                        seen[j] = 1
                        stack.append((nx, ny))
        if n >= 120:
            blocks.append((n, bx0, by0, bx1, by1))
blocks.sort(reverse=True)
if not blocks:
    lines.append("  没有 >=120px 的白色连通块")
for n, bx0, by0, bx1, by1 in blocks[:14]:
    bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
    fill = n / float(bw * bh) * 100
    lines.append("  %6d px  bbox=(%3d,%3d)-(%3d,%3d)  %3dx%-3d  填充率 %5.1f%%"
                 % (n, bx0, by0, bx1, by1, bw, bh, fill))

# ---------- 4) 直方图 ----------
cnt = Counter(img.getdata())
lines.append("")
lines.append("=== 4) 出现最多的 10 种颜色 ===")
for (r, g, b), n in cnt.most_common(10):
    lines.append("  #%02X%02X%02X  %7d px  %5.2f%%" % (r, g, b, n, n / float(W * H) * 100))

# ---------- 5) 关键点 ----------
lines.append("")
lines.append("=== 5) 关键点 ===")
pts = [("图左上", 1, 1), ("图右上", W - 2, 1), ("图左下", 1, H - 2), ("图右下", W - 2, H - 2)]
if x0 is not None:
    pts += [("黑区左上内", x0 + 6, y0 + 6), ("黑区右上内", x1 - 6, y0 + 6),
            ("黑区左下内", x0 + 6, y1 - 6), ("黑区右下内", x1 - 6, y1 - 6),
            ("黑区中心", (x0 + x1) // 2, (y0 + y1) // 2)]
for name, x, y in pts:
    r, g, b = px[x, y]
    lines.append("  %-10s (%3d,%3d) = #%02X%02X%02X" % (name, x, y, r, g, b))

text = "\n".join(lines)
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(text + "\n")
print("written", OUT)
