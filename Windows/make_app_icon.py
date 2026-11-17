"""
make_app_icon.py -- draws MAICA's app icon: its green idle face, two dark
eyes with a glint each, on a rounded square.

    python3 make_app_icon.py OUT.iconset     a macOS icon set (build_mac.sh turns it
                                             into MAICA.icns with iconutil)
    python3 make_app_icon.py OUT.ico         a Windows icon

Needs Pillow.
"""

import os
import sys

from PIL import Image, ImageDraw

GREEN, DARK, GLINT = (40, 220, 90), (8, 22, 12), (210, 245, 220)


def draw(size: int) -> Image.Image:
    s = 2048  # drawn big, scaled down smooth
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = s * 0.06  # macOS icons leave a margin around the rounded square
    d.rounded_rectangle((m, m, s - m, s - m), radius=s * 0.2, fill=GREEN + (255,))
    ew, eh = s * 0.105, s * 0.17
    for cx in (s * 0.355, s * 0.645):
        cy = s * 0.5
        d.rounded_rectangle((cx - ew, cy - eh, cx + ew, cy + eh), radius=ew * 0.8, fill=DARK + (255,))
        g = ew * 0.42
        gx, gy = cx - ew * 0.3, cy - eh * 0.45
        d.ellipse((gx - g, gy - g, gx + g, gy + g), fill=GLINT + (255,))
    return img.resize((size, size), Image.LANCZOS)


def iconset(out: str):
    os.makedirs(out, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        draw(base).save(os.path.join(out, f"icon_{base}x{base}.png"))
        draw(base * 2).save(os.path.join(out, f"icon_{base}x{base}@2x.png"))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    out = argv[1]
    if out.endswith(".iconset"):
        iconset(out)
    else:
        draw(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
