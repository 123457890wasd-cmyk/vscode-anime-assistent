# -*- coding: utf-8 -*-
"""读用户截图的像素，判定窗口背景到底是「WebView2 白」还是「WinForms 灰」。

为什么必须分辨这两者 —— 它们的修复方向完全相反：

  #FFFFFF  -> WebView2 的 DefaultBackgroundColor 仍是白 → 透明压根没设上
  #F0F0F0  -> Form.BackColor = SystemColors.Control → WebView2 那层**已经透明**，
              是宿主 WinForms Form 挡在后面（修复只需处理 Form 那一层）

pywebview 6.2.1 的 winforms 实现里，transparent 分支只调了
SetStyle(SupportsTransparentBackColor, True)，**没有** BackColor = Transparent
（那句在 else 分支里），所以 Form 很可能还留着默认的系统色。
"""
import sys
from collections import Counter

try:
    from PIL import Image
except ImportError:
    sys.exit("PIL not installed")

SRC = r"C:\Users\Mr.hancard\.workbuddy\clipboard-images\clipboard-2026-09-22T13-44-59-871Z-9a6bf1e7.png"
OUT = r"S:\My event\projects\vscode-anime-assistent\live2d_probe\shot_pixels.txt"

lines = []
img = Image.open(SRC).convert("RGB")
W, H = img.size
lines.append(f"image size = {W} x {H}")

lines.append("")
lines.append("=== 网格采样（每 30px，格式 x,y=#RRGGBB）===")
for y in range(0, H, 30):
    row = []
    for x in range(0, W, 30):
        r, g, b = img.getpixel((x, y))
        row.append("%3d,%3d=%02X%02X%02X" % (x, y, r, g, b))
    lines.append("  " + " ".join(row))

cnt = Counter(img.getdata())
tot = W * H
lines.append("")
lines.append("=== 出现最多的 12 种颜色 ===")
for (r, g, b), n in cnt.most_common(12):
    lines.append("  #%02X%02X%02X  %7d px  %5.2f%%" % (r, g, b, n, n / tot * 100.0))

lines.append("")
lines.append("=== 关键点 ===")
pts = [("左上角", 1, 1), ("右上角", W - 2, 1), ("左下角", 1, H - 2), ("右下角", W - 2, H - 2),
       ("正中", W // 2, H // 2), ("上中", W // 2, 3), ("下中", W // 2, H - 4)]
for name, x, y in pts:
    r, g, b = img.getpixel((x, y))
    lines.append("  %-8s (%3d,%3d) = #%02X%02X%02X" % (name, x, y, r, g, b))

lines.append("")
lines.append("=== 判定 ===")
# 取窗口内部靠上但避开模型的区域来判定"窗口底色"
sample = []
for y in range(10, max(11, H // 4), 5):
    for x in range(10, W - 10, 10):
        sample.append(img.getpixel((x, y)))
if sample:
    sc = Counter(sample).most_common(1)[0]
    (r, g, b), n = sc
    lines.append("  窗口上部主色 = #%02X%02X%02X (%d/%d 采样点)" % (r, g, b, n, len(sample)))
    if (r, g, b) == (255, 255, 255):
        lines.append("  => 结论：纯白。WebView2 的 DefaultBackgroundColor 没有被设成 Transparent，")
        lines.append("     或者设了之后又被 CoreWebView2 初始化覆盖。属于「WebView2 层没透」。")
    elif abs(r - 240) <= 8 and abs(g - 240) <= 8 and abs(b - 240) <= 8:
        lines.append("  => 结论：接近 #F0F0F0 = SystemColors.Control。")
        lines.append("     WebView2 层已经透明了，是宿主 WinForms Form 的 BackColor 挡住了。")
        lines.append("     属 pywebview 的实现遗漏（transparent 分支不设 BackColor）。")
    else:
        lines.append("  => 结论：既不是纯白也不是系统灰，见上面的直方图（可能是浅色主题下的其它系统色）。")

text = "\n".join(lines)
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(text + "\n")
print(text)
