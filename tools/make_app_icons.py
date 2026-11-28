"""Mobilappens ikoner: löparbanan med en pulslinje genom sig.

Kör från repots rot:  python3 tools/make_app_icons.py

SVG:n nedan är enda källan. PNG-filerna renderas med headless Chrome, eftersom
PIL inte kan rita SVG. Webbsidans ikoner (public/favicon.svg m.fl.) rörs inte;
de görs av tools/make_icons.py.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'public' / 'app' / 'icons'

GRADIENT = ('<linearGradient id="tz-plate" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#2F7BFF"/><stop offset="1" stop-color="#0B2F8A"/>'
            '</linearGradient>')
# Banan (halvgenomskinlig) och pulsen (vit). Koordinater i en 512-ruta.
TRACK = '<rect x="88" y="168" width="336" height="176" rx="88" stroke-width="30" stroke-opacity=".5"/>'
PULSE = '<path d="M116 300 H196 L234 216 L282 326 L318 256 H396" stroke-width="34"/>'
MARK = (f'<g fill="none" stroke="#FFFFFF" stroke-linecap="round" stroke-linejoin="round">'
        f'{TRACK}{PULSE}</g>')


def svg(rounded=True, scale=1.0):
    """Ikonen som SVG. `scale` krymper märket mot mitten (maskable-ikonen)."""
    radius = ' rx="112"' if rounded else ''
    mark = MARK if scale == 1 else (
        f'<g transform="translate({256 * (1 - scale):.1f} {256 * (1 - scale):.1f}) scale({scale})">{MARK}</g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
            f'<defs>{GRADIENT}</defs><rect width="512" height="512"{radius} fill="url(#tz-plate)"/>{mark}</svg>')


def render(svg_text, size, target):
    chrome = shutil.which('google-chrome') or '/opt/google/chrome/chrome'
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / 'icon.html'
        page.write_text('<html><body style="margin:0;background:transparent">'
                        f'<div style="width:{size}px;height:{size}px">'
                        f'{svg_text.replace("<svg ", f"<svg width={size} height={size} ", 1)}'
                        '</div></body></html>', encoding='utf-8')
        subprocess.run([chrome, '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
                        '--default-background-color=00000000', f'--window-size={size},{size}',
                        f'--screenshot={target}', page.as_uri()],
                       check=True, capture_output=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'icon.svg').write_text(svg(rounded=True), encoding='utf-8')
    # iOS och Android rundar själva hörnen: hemskärmsikonerna ska vara fyrkantiga.
    for size, name in ((180, 'apple-touch-icon.png'), (192, 'icon-192.png'), (512, 'icon-512.png')):
        render(svg(rounded=False), size, OUT / name)
    render(svg(rounded=False, scale=0.8), 512, OUT / 'maskable-512.png')
    render(svg(rounded=True), 64, OUT / 'favicon-64.png')
    print('Skrev', ', '.join(sorted(p.name for p in OUT.iterdir())))


if __name__ == '__main__':
    main()
