#!/usr/bin/env python3
"""Point at the thing that matters in a screenshot: a box around it, an arrow to it, a label.

Usage: python3 -I annotate.py <in.png> <out.png> <x> <y> <w> <h> ["label"]
  x y w h: the element's bounding box in screenshot pixels (Playwright's locator.boundingBox()).
Draws on the image file only; the page is never touched. Needs Pillow.
Write the output as .png: it is marked as annotated, which check_evidence.py requires.
A label longer than 32 characters is cut, so keep it to the value that changed.
"""
import math
import sys
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

RED = (220, 38, 38)


def font(size):
    for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                 '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
                 '/System/Library/Fonts/Supplemental/Arial Bold.ttf'):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main():
    src, dst = sys.argv[1], sys.argv[2]
    x, y, w, h = (float(v) for v in sys.argv[3:7])
    label = sys.argv[7] if len(sys.argv) > 7 else ''
    if len(label) > 32:  # the PR text carries the explanation; the label only names the value
        label = label[:31] + '…'
    img = Image.open(src).convert('RGB')
    W, H = img.size
    d = ImageDraw.Draw(img)
    pad = 6
    box = (max(x - pad, 2), max(y - pad, 2), min(x + w + pad, W - 3), min(y + h + pad, H - 3))
    d.rectangle(box, outline=RED, width=3)

    f = font(18)
    tw, th = (d.textbbox((0, 0), label, font=f)[2:] if label else (0, 0))
    # Put the label on the side with more room; the arrow runs from the label to the box.
    cy = (box[1] + box[3]) / 2
    right_room, left_room = W - box[2], box[0]
    gap = 110
    if right_room >= left_room:
        tip = (box[2] + 4, cy)
        tail = (min(box[2] + gap, W - tw - 24), cy - 70 if cy > 110 else cy + 70)
        lx = min(tail[0] + 10, W - tw - 14)
    else:
        tip = (box[0] - 4, cy)
        tail = (max(box[0] - gap, tw + 24), cy - 70 if cy > 110 else cy + 70)
        lx = max(tail[0] - tw - 10, 14)
    ly = min(max(tail[1] - th / 2, 8), H - th - 14)

    d.line([tail, tip], fill=RED, width=5)
    ang = math.atan2(tip[1] - tail[1], tip[0] - tail[0])
    head = 20
    d.polygon([tip,
               (tip[0] - head * math.cos(ang - 0.45), tip[1] - head * math.sin(ang - 0.45)),
               (tip[0] - head * math.cos(ang + 0.45), tip[1] - head * math.sin(ang + 0.45))], fill=RED)
    if label:
        d.rectangle((lx - 8, ly - 6, lx + tw + 8, ly + th + 8), fill=(255, 255, 255), outline=RED, width=2)
        d.text((lx, ly), label, fill=RED, font=f)
    if dst.lower().endswith('.png'):
        # check_evidence.py looks for this chunk to tell an annotated shot from a raw one.
        mark = PngImagePlugin.PngInfo()
        mark.add_text('annotation', label or 'box')
        img.save(dst, pnginfo=mark)
    else:
        img.save(dst)


if __name__ == '__main__':
    main()
