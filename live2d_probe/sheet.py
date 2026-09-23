"""_sheet.py — 把各底板变体的预览图并排拼成一张带标签的对比图。

用法：python _sheet.py <out.png> "<标签1>=<文件1>" "<标签2>=<文件2>" ...
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyhbd.ttc',
         r'C:\Windows\Fonts\simhei.ttf']


def font(size):
    for f in FONTS:
        if Path(f).is_file():
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
    return ImageFont.load_default()


out = Path(sys.argv[1])
items = []
for spec in sys.argv[2:]:
    label, _, path = spec.partition('=')
    items.append((label, Image.open(path).convert('RGB')))

PAD = 10
STRIP = 34
font_l = font(17)
font_s = font(12)

W = sum(im.width for _, im in items) + PAD * (len(items) + 1)
H = max(im.height for _, im in items) + STRIP + PAD * 2
sheet = Image.new('RGB', (W, H), (250, 250, 252))
d = ImageDraw.Draw(sheet)

x = PAD
for label, im in items:
    d.rectangle([x - 1, PAD + STRIP - 1, x + im.width, PAD + STRIP + im.height],
                outline=(190, 190, 196))
    sheet.paste(im, (x, PAD + STRIP))
    d.text((x + 2, PAD + 4), label, fill=(28, 28, 32), font=font_l)
    x += im.width + PAD

sheet.save(out)
print(out, sheet.size)
