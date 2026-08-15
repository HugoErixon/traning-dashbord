"""Generate Trainyze's favicons from the brand mark.

Run from the repo root:  python3 tools/make_icons.py

The mark is a running track seen from above — four concentric lanes in the
vitt på en koboltblå platta. Both the raster set and the SVG are
produced from the constants below, so the two can never drift apart.

At 16 px the lanes merge into a single pale oval, which is what should
happen: that size only has to be a recognisable silhouette, and a solid
blue shape reads far better than fine lines ever could.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (255, 255, 255, 255)     # banan stansas ur plattan
PLATE = (18, 86, 224, 255)        # --accent (kobolt)
SUPERSAMPLE = 8                   # draw large, downscale for clean edges

LANES = 4
PLATE_RADIUS = 0.22               # share of the icon's width
# Padding of the outermost and innermost lane, as shares of the icon size.
OUTER_PAD = (0.075, 0.245)
INNER_PAD = (0.325, 0.418)
STROKE = 0.056


def _lane_geometry():
    """Padding for each lane, in shares of the icon size."""
    for index in range(LANES):
        t = index / max(1, LANES - 1)
        yield (OUTER_PAD[0] + (INNER_PAD[0] - OUTER_PAD[0]) * t,
               OUTER_PAD[1] + (INNER_PAD[1] - OUTER_PAD[1]) * t)


def draw_icon(size):
    """Render one square icon at `size` pixels."""
    c = size * SUPERSAMPLE
    image = Image.new('RGBA', (c, c), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, c - 1, c - 1), radius=int(c * PLATE_RADIUS), fill=PLATE)

    for pad_x, pad_y in _lane_geometry():
        box = (c * pad_x, c * pad_y, c - c * pad_x, c - c * pad_y)
        draw.rounded_rectangle(box, radius=(box[3] - box[1]) / 2,
                               outline=ACCENT, width=int(c * STROKE))

    return image.resize((size, size), Image.LANCZOS)


def _hex(rgba):
    """RGBA-konstant till hexkod, så SVG:n och rasterbilderna delar färg."""
    return '#{:02X}{:02X}{:02X}'.format(*rgba[:3])


def build_svg():
    """The same track as scalable vector, for browsers that prefer it."""
    view = 64
    stroke = STROKE * view
    lanes = []
    for pad_x, pad_y in _lane_geometry():
        x, y = pad_x * view, pad_y * view
        w, h = view - 2 * x, view - 2 * y
        # SVG centres a stroke on its path while Pillow draws it inside the
        # box, so inset by half a stroke to land in the same place.
        x, y = x + stroke / 2, y + stroke / 2
        w, h = w - stroke, h - stroke
        lanes.append(
            f'    <rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'rx="{h / 2:.2f}"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view} {view}" '
        f'role="img" aria-label="Trainyze">\n'
        f'  <rect width="{view}" height="{view}" rx="{PLATE_RADIUS * view:.1f}" fill="{_hex(PLATE)}"/>\n'
        f'  <g fill="none" stroke="{_hex(ACCENT)}" stroke-width="{stroke:.2f}">\n'
        + '\n'.join(lanes) + '\n'
        f'  </g>\n</svg>\n'
    )


def main():
    out = Path(__file__).resolve().parents[1] / 'public'
    out.mkdir(exist_ok=True)

    draw_icon(180).save(out / 'apple-touch-icon.png')
    draw_icon(192).save(out / 'icon-192.png')
    draw_icon(512).save(out / 'icon-512.png')
    # .ico carries several sizes; the browser picks what it needs.
    draw_icon(64).save(out / 'favicon.ico', sizes=[(16, 16), (32, 32), (48, 48)])
    (out / 'favicon.svg').write_text(build_svg(), encoding='utf-8')

    print('Ikoner skrivna till', out)


if __name__ == '__main__':
    main()
