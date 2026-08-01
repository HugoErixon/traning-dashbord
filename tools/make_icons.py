"""Generate Trainyze's favicons from the brand mark.

Run from the repo root:  python3 tools/make_icons.py

The mark is the same pulse line the landing page uses, set in the brand
green on a dark rounded square. A filled square reads as a distinct blob
at 16 px, which is the size that actually decides whether an icon is
recognisable in a tab strip or bookmarks bar.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (200, 241, 53, 255)   # --accent
INK = (18, 26, 0, 255)         # dark ink for the glyph
SUPERSAMPLE = 8                # draw large, downscale for clean edges

# The pulse polyline, in the 28-unit space the landing page SVG uses.
PULSE = [(2.6, 15.5), (7.2, 15.5), (10.1, 7.3), (14.2, 22.3),
         (17.2, 12.9), (19.6, 17.2), (25.4, 17.2)]


def draw_icon(size, padding_ratio=0.0, radius_ratio=0.22, transparent_bg=False):
    """Render one square icon at `size` pixels."""
    canvas = size * SUPERSAMPLE
    pad = int(canvas * padding_ratio)
    image = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    box = (pad, pad, canvas - pad - 1, canvas - pad - 1)
    if not transparent_bg:
        draw.rounded_rectangle(box, radius=int(canvas * radius_ratio), fill=ACCENT)

    inner = box[2] - box[0]
    scale = inner / 28.0
    points = [(box[0] + x * scale, box[1] + y * scale) for x, y in PULSE]
    # Thick enough that the glyph survives being shrunk to 16 px.
    width = max(1, int(inner * 0.105))
    draw.line(points, fill=ACCENT if transparent_bg else INK,
              width=width, joint='curve')
    # Round the open ends, which draw.line leaves square.
    for x, y in (points[0], points[-1]):
        r = width / 2
        draw.ellipse((x - r, y - r, x + r, y + r),
                     fill=ACCENT if transparent_bg else INK)

    return image.resize((size, size), Image.LANCZOS)


def main():
    out = Path(__file__).resolve().parents[1] / 'public'
    out.mkdir(exist_ok=True)

    draw_icon(180, padding_ratio=0.0).save(out / 'apple-touch-icon.png')
    draw_icon(192).save(out / 'icon-192.png')
    draw_icon(512).save(out / 'icon-512.png')

    # .ico carries several sizes; Windows and older browsers pick what they need.
    draw_icon(64).save(out / 'favicon.ico', sizes=[(16, 16), (32, 32), (48, 48)])
    print('Ikoner skrivna till', out)


if __name__ == '__main__':
    main()
