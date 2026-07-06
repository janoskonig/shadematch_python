"""Colour-space regions for the learning-effect measurement.

A region is a fixed CIELAB grid cell with zone-dependent resolution: the Xiao
skin zone (the densified 'even_gamut_v2_skin' targets) is diced finely, the rest
of the gamut coarsely. Learning is measured per (user, region): as a player
accumulates exposures to *different* colours inside a region, we fit their
accuracy trajectory — capturing transfer/generalisation, not recipe memorisation.

`region_of(...)` is a pure function of a colour (+ its skin flag), so no schema
change is needed: serving and analysis both derive the region on the fly.
"""

SKIN_CELL = 18.0    # CIELAB cell size inside the skin zone (fine)
BG_CELL = 36.0      # CIELAB cell size elsewhere (coarse)
TARGET_EXPOSURES_PER_REGION = 8   # exposures before a region is "learned enough"
# → 54 regions over the 332 gamut targets (median 5 colours/region). ~10 gamut-edge
#   colours land in singleton (1-colour) cells; those are played normally but never
#   force-revisited (a region-learning curve needs *different* colours in the cell).


def _srgb_to_lab(r, g, b):
    """8-bit sRGB → CIELAB (D65, 2°). Small, dependency-free (no numpy/scipy)."""
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    R, G, B = lin(r), lin(g), lin(b)
    X = R * 0.4124564 + G * 0.3575761 + B * 0.1804375
    Y = R * 0.2126729 + G * 0.7151522 + B * 0.0721750
    Z = R * 0.0193339 + G * 0.1191920 + B * 0.9503041
    xr, yr, zr = X / 0.95047, Y / 1.0, Z / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856451679035631 else 7.787037037037037 * t + 16 / 116
    fx, fy, fz = f(xr), f(yr), f(zr)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def region_of_lab(L, a, b, is_skin):
    """Region id for a CIELAB colour. Cell size depends on the zone."""
    cell = SKIN_CELL if is_skin else BG_CELL
    zone = 'skin' if is_skin else 'bg'
    return '%s:%d_%d_%d' % (zone,
                            int(L // cell), int(a // cell), int(b // cell))


def is_skin_target(tc):
    """A gamut target sits in the skin zone iff it carries the skin classification."""
    return getattr(tc, 'classification', None) == 'even_gamut_v2_skin'


def region_of_target(tc):
    """Region id for a TargetColor row (uses its stored RGB + skin classification)."""
    L, a, b = _srgb_to_lab(tc.r, tc.g, tc.b)
    return region_of_lab(L, a, b, is_skin_target(tc))
