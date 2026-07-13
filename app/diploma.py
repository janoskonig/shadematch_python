"""Server-rendered printable diploma (A4 portrait PNG) for players who have
mastered every region of the colour gamut.

Pillow, sized for print (A4 @ 300 DPI). Serif typography, a soft parchment
vignette, ornamental gold border, and a colour-wheel medallion built from the
player's *real* mastered colours — the centrepiece that ties the award to
"mastered the whole gamut". All strings arrive already localized; the route
owns eligibility.
"""
import io
import math
import os

from PIL import Image, ImageDraw, ImageFont

# A4 portrait @ 300 DPI.
W, H = 2480, 3508

PAPER_HI = (252, 250, 244)      # vignette centre
PAPER_LO = (235, 226, 206)      # vignette edge
SEAL_FILL = (249, 245, 236)     # medallion centre disc
INK = (38, 32, 24)
INK_SOFT = (122, 112, 92)
GOLD = (176, 141, 62)
GOLD_HI = (206, 174, 96)
RULE = (198, 182, 150)

_FONTS = {
    'serif': 'DejaVuSerif.ttf',
    'serif-bold': 'DejaVuSerif-Bold.ttf',
    'serif-italic': 'DejaVuSerif-Italic.ttf',
}


def _font_dir():
    import matplotlib
    return os.path.join(os.path.dirname(matplotlib.__file__),
                        'mpl-data', 'fonts', 'ttf')


def _font(size, style='serif'):
    return ImageFont.truetype(os.path.join(_font_dir(), _FONTS[style]), size)


def _fit(draw, text, max_width, size, style='serif', min_size=30):
    while size > min_size:
        f = _font(size, style)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 4
    return _font(min_size, style)


def _centered(draw, y, text, font, fill):
    draw.text(((W - draw.textlength(text, font=font)) / 2, y), text,
              font=font, fill=fill)


def _centered_on(draw, cx, y, text, font, fill):
    draw.text((cx - draw.textlength(text, font=font) / 2, y), text,
              font=font, fill=fill)


def _tracked(draw, cx, y, text, font, fill, track):
    """Draw letter-spaced text centred on cx (small-caps refinement)."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + track * (len(text) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + track


def _parchment():
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    d = np.clip(d / 1.28, 0, 1)[..., None]
    c0 = np.array(PAPER_HI, dtype=float)
    c1 = np.array(PAPER_LO, dtype=float)
    return Image.fromarray((c0 * (1 - d) + c1 * d).astype('uint8'), 'RGB')


def _corner(d, x, y, sx, sy):
    """Small gold fleuron at an inner-border corner. (sx, sy) point inward."""
    # short double rules
    d.line([(x, y), (x + 130 * sx, y)], fill=GOLD, width=5)
    d.line([(x, y), (x, y + 130 * sy)], fill=GOLD, width=5)
    d.line([(x + 34 * sx, y + 34 * sy), (x + 150 * sx, y + 34 * sy)], fill=RULE, width=3)
    d.line([(x + 34 * sx, y + 34 * sy), (x + 34 * sx, y + 150 * sy)], fill=RULE, width=3)
    # diamond
    cxp, cyp = x + 34 * sx, y + 34 * sy
    r = 17
    d.polygon([(cxp, cyp - r), (cxp + r, cyp), (cxp, cyp + r), (cxp - r, cyp)],
              fill=GOLD_HI)


def _divider(d, cx, y, half):
    """Horizontal rule with a centred diamond and end dots."""
    d.line([(cx - half, y), (cx - 34, y)], fill=RULE, width=3)
    d.line([(cx + 34, y), (cx + half, y)], fill=RULE, width=3)
    r = 13
    d.polygon([(cx, y - r), (cx + r, y), (cx, y + r), (cx - r, y)], fill=GOLD)
    for sign in (-1, 1):
        d.ellipse([cx + sign * half - 6, y - 6, cx + sign * half + 6, y + 6], fill=GOLD)


def _medallion(d, cx, cy, colours, number, caption):
    """Colour-wheel ring built from the player's real colours, with gold rings
    and a centre disc showing the region count."""
    R_out, R_in = 430, 258
    n = max(1, len(colours))
    seg = 360.0 / n
    for i, rgb in enumerate(colours):
        a0 = -90 + i * seg
        d.pieslice([cx - R_out, cy - R_out, cx + R_out, cy + R_out],
                   a0 - 0.5, a0 + seg + 0.5, fill=tuple(int(c) for c in rgb))
    # centre disc (covers the pie hole)
    d.ellipse([cx - R_in, cy - R_in, cx + R_in, cy + R_in], fill=SEAL_FILL)
    # gold rings
    for rr, wdt, col in ((R_out + 10, 12, GOLD), (R_out + 30, 4, GOLD_HI),
                         (R_in, 10, GOLD), (R_in - 16, 4, GOLD_HI)):
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=col, width=wdt)
    # centre content: big number, centred, with caption fitted inside the disc
    if number:
        nf = _font(180, 'serif-bold')
        nb = d.textbbox((0, 0), number, font=nf)
        d.text((cx - (nb[0] + nb[2]) / 2, cy - 50 - (nb[1] + nb[3]) / 2),
               number, font=nf, fill=INK)
    if caption:
        cap = caption.upper()
        max_w = 2 * R_in - 96
        size, track = 44, 5
        while size > 22:
            cf = _font(size, 'serif')
            total = sum(d.textlength(ch, font=cf) for ch in cap) + track * (len(cap) - 1)
            if total <= max_w:
                break
            size -= 3
        _tracked(d, cx, cy + 118, cap, cf, INK_SOFT, track)


def _stats_row(d, y, stats):
    """A centred row of (value, label) tiles with thin dividers between them."""
    margin, n = 300, len(stats)
    colw = (W - 2 * margin) / n
    for i, (val, lab) in enumerate(stats):
        cxi = margin + colw * (i + 0.5)
        vf = _font(92, 'serif-bold')
        d.text((cxi - d.textlength(str(val), font=vf) / 2, y), str(val),
               font=vf, fill=INK)
        cap = str(lab).upper()
        size, track = 40, 5
        while size > 20:
            cf = _font(size, 'serif')
            total = sum(d.textlength(ch, font=cf) for ch in cap) + track * (len(cap) - 1)
            if total <= colw - 70:
                break
            size -= 2
        _tracked(d, cxi, y + 120, cap, cf, INK_SOFT, track)
        if i > 0:
            xd = margin + colw * i
            d.line([(xd, y + 8), (xd, y + 150)], fill=RULE, width=2)


def render_diploma(*, title, subtitle, certifies_line, player_name,
                   achievement_lines, date_line, footer_line,
                   author_signature, author_title, date_label,
                   stats=None, swatch_rgbs=None, seal_number=None, seal_caption=None):
    """Return PNG bytes. `achievement_lines` is a list of already-localized
    lines; `stats` is a list of (value, label); `swatch_rgbs` (r,g,b tuples)
    build the colour-wheel medallion; `seal_number`/`seal_caption` fill its
    centre; `author_signature`/`author_title`/`date_label` fill the footer."""
    img = _parchment()
    d = ImageDraw.Draw(img)
    cx = W // 2

    # ── Ornamental border ────────────────────────────────────────────────
    d.rectangle([78, 78, W - 78, H - 78], outline=GOLD, width=9)
    d.rectangle([112, 112, W - 112, H - 112], outline=RULE, width=3)
    _corner(d, 112, 112, 1, 1)
    _corner(d, W - 112, 112, -1, 1)
    _corner(d, 112, H - 112, 1, -1)
    _corner(d, W - 112, H - 112, -1, -1)

    # ── Brand + title ────────────────────────────────────────────────────
    _tracked(d, cx, 300, 'SHADEMATCH', _font(58, 'serif'), GOLD, 16)
    _centered(d, 402, title, _font(168, 'serif-bold'), INK)
    sf = _fit(d, subtitle, W - 560, 80, 'serif-italic', min_size=46)
    _centered(d, 636, subtitle, sf, INK_SOFT)
    _divider(d, cx, 812, 300)

    # ── Recipient ────────────────────────────────────────────────────────
    _centered(d, 918, certifies_line, _font(62, 'serif-italic'), INK_SOFT)
    name_font = _fit(d, player_name, W - 620, 210, 'serif-bold', min_size=96)
    nw = d.textlength(player_name, font=name_font)
    ny = 1030
    _centered(d, ny, player_name, name_font, INK)
    _divider(d, cx, ny + name_font.size + 60, int(nw / 2 + 60))

    # ── Achievement prose ────────────────────────────────────────────────
    y = 1364
    for line in achievement_lines:
        lf = _fit(d, line, W - 560, 74, 'serif', min_size=44)
        _centered(d, y, line, lf, INK)
        y += 112

    # ── Colour-wheel medallion (centrepiece) ─────────────────────────────
    if swatch_rgbs:
        _medallion(d, cx, 2050, list(swatch_rgbs), seal_number, seal_caption)

    # ── Player stat tiles ────────────────────────────────────────────────
    if stats:
        _stats_row(d, 2640, stats)

    # ── Signature + date footer ──────────────────────────────────────────
    fy = H - 420
    lx0, lx1 = 300, 960
    d.line([(lx0, fy), (lx1, fy)], fill=INK, width=3)
    if author_signature:
        sgf = _font(84, 'serif-italic')
        _centered_on(d, (lx0 + lx1) / 2, fy - 104,
                     author_signature, sgf, INK)
    if author_title:
        _centered_on(d, (lx0 + lx1) / 2, fy + 22, author_title,
                     _font(40, 'serif'), INK_SOFT)

    rx0, rx1 = W - 960, W - 300
    d.line([(rx0, fy), (rx1, fy)], fill=INK, width=3)
    _centered_on(d, (rx0 + rx1) / 2, fy - 92, date_line, _font(64, 'serif'), INK)
    if date_label:
        _centered_on(d, (rx0 + rx1) / 2, fy + 22, date_label,
                     _font(40, 'serif'), INK_SOFT)

    _tracked(d, cx, H - 190, footer_line, _font(52, 'serif'), GOLD, 4)

    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()
