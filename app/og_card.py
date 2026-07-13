"""Server-rendered Open Graph preview card (1200x630 PNG) for challenge links.

Dark ground, big target swatch, DeltaE headline, and time — the bar the
recipient has to beat. It deliberately shows nothing about *how* the creator
solved it (no drop count, no step-by-step trajectory), so the preview can't be
used to shortcut the round.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

GROUND = (21, 18, 33)
INK = (255, 255, 255)
INK_SOFT = (255, 255, 255, 140)
ACCENT = (255, 209, 102)

W, H = 1200, 630


def _font(size, bold=True):
    # DejaVu ships with matplotlib (already in requirements); covers Greek
    # (DeltaE) and Hungarian diacritics.
    import matplotlib
    base = os.path.join(os.path.dirname(matplotlib.__file__),
                        'mpl-data', 'fonts', 'ttf')
    name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    return ImageFont.truetype(os.path.join(base, name), size)


def _fit_text(draw, text, max_width, size, min_size=30, bold=True):
    """Largest font <= size that fits text in max_width."""
    while size > min_size:
        f = _font(size, bold)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 4
    return _font(min_size, bold)


def render_challenge_card(target_rgb, color_name, delta_e, stats_line,
                          footer):
    """Return PNG bytes. All strings arrive already localized."""
    img = Image.new('RGB', (W, H), GROUND)
    d = ImageDraw.Draw(img)

    # Target swatch, left half.
    sw = [64, 64, 64 + 440, H - 64]
    d.rounded_rectangle(sw, radius=36, fill=tuple(target_rgb))
    d.rounded_rectangle(sw, radius=36, outline=(255, 255, 255), width=3)

    x = 570
    right_w = W - x - 64

    d.text((x, 72), 'ShadeMatch', font=_font(44), fill=INK)

    if color_name:
        f = _fit_text(d, color_name, right_w, 54)
        d.text((x, 150), color_name, font=f, fill=(226, 222, 235))

    if delta_e is not None:
        d.text((x, 236), f'ΔE {delta_e:.2f}', font=_font(112), fill=ACCENT)

    if stats_line:
        d.text((x, 392), stats_line, font=_font(40, bold=False),
               fill=(180, 176, 194))

    if footer:
        f = _fit_text(d, footer, right_w, 40, min_size=26)
        d.text((x, 540), footer, font=f, fill=(180, 176, 194))

    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()
