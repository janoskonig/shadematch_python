"""Server-rendered printable diploma (A4 portrait PNG) for players who have
mastered every region of the colour gamut.

Same Pillow technique as og_card.py — DejaVu fonts (Hungarian diacritics
covered) — but sized for print (A4 @ 300 DPI) and styled like a certificate.
All strings arrive already localized; the route owns eligibility.
"""
import io

from PIL import Image, ImageDraw

from .og_card import _font, _fit_text

# A4 portrait @ 300 DPI.
W, H = 2480, 3508

PAPER = (250, 247, 240)
INK = (34, 30, 24)
INK_SOFT = (120, 112, 96)
GOLD = (176, 141, 62)
RULE = (198, 182, 150)


def _centered(draw, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)


def render_diploma(*, title, subtitle, certifies_line, player_name,
                   achievement_lines, signature_label, date_line, footer_line,
                   swatch_rgbs=None):
    """Return PNG bytes. `achievement_lines` is a list of already-localized
    lines shown under the recipient's name; `swatch_rgbs` is an optional list of
    (r, g, b) tuples drawn as a colour strip."""
    img = Image.new('RGB', (W, H), PAPER)
    d = ImageDraw.Draw(img)
    cx = W // 2

    # ── Ornamental double border ─────────────────────────────────────────
    d.rectangle([70, 70, W - 70, H - 70], outline=GOLD, width=8)
    d.rectangle([104, 104, W - 104, H - 104], outline=RULE, width=3)

    # ── Brand kicker + title ─────────────────────────────────────────────
    _centered(d, 300, 'ShadeMatch', _font(70), GOLD)
    _centered(d, 430, title, _font(150), INK)

    f = _fit_text(d, subtitle, W - 520, 72, min_size=44, bold=False)
    _centered(d, 660, subtitle, f, INK_SOFT)
    d.line([(cx - 260, 800), (cx + 260, 800)], fill=RULE, width=3)

    # ── Recipient ────────────────────────────────────────────────────────
    _centered(d, 940, certifies_line, _font(64, bold=False), INK_SOFT)

    name_font = _fit_text(d, player_name, W - 600, 200, min_size=90)
    nw = d.textlength(player_name, font=name_font)
    ny = 1060
    _centered(d, ny, player_name, name_font, INK)
    underline_y = ny + name_font.size + 34
    d.line([(cx - nw / 2 - 24, underline_y), (cx + nw / 2 + 24, underline_y)],
           fill=GOLD, width=5)

    # ── Achievement prose ────────────────────────────────────────────────
    y = 1420
    for line in achievement_lines:
        lf = _fit_text(d, line, W - 520, 72, min_size=42, bold=False)
        _centered(d, y, line, lf, INK)
        y += 118

    # ── Colour strip of the real mastered targets ────────────────────────
    if swatch_rgbs:
        strip = list(swatch_rgbs)[:24]
        n = len(strip)
        margin, gap, sy, sh = 320, 16, 1860, 230
        avail = W - 2 * margin
        cw = (avail - gap * (n - 1)) / n
        for i, rgb in enumerate(strip):
            x0 = margin + i * (cw + gap)
            d.rounded_rectangle([x0, sy, x0 + cw, sy + sh], radius=18,
                                fill=tuple(int(c) for c in rgb))
        d.rounded_rectangle([margin - 12, sy - 12, W - margin + 12, sy + sh + 12],
                            radius=26, outline=RULE, width=3)

    # ── Signature + date footer ──────────────────────────────────────────
    fy = H - 560
    small = _font(48, bold=False)
    left_x, right_x = 320, W - 320
    d.line([(left_x, fy), (left_x + 640, fy)], fill=INK, width=3)
    d.text((left_x, fy + 26), signature_label, font=small, fill=INK_SOFT)
    dw = d.textlength(date_line, font=small)
    d.line([(right_x - 640, fy), (right_x, fy)], fill=INK, width=3)
    d.text((right_x - dw, fy + 26), date_line, font=small, fill=INK_SOFT)

    _centered(d, H - 280, footer_line, _font(52), GOLD)

    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()
