"""Characterization tests for app/color_drops.py (the de-dup refactor).

Locks the two summation conventions and the canonical channel ordering so the
remaining Phase-1 colour-science extraction can rely on them.
"""
from app.color_drops import PAINT_CHANNELS, sum_drops, sum_drops_strict


class _Row:
    def __init__(self, prefix="drop_", **vals):
        for name in PAINT_CHANNELS:
            setattr(self, prefix + name, vals.get(name))


def test_channel_ordering_is_canonical():
    assert PAINT_CHANNELS == ("white", "black", "red", "yellow", "blue")


def test_sum_drops_treats_none_as_zero():
    row = _Row(white=1, black=2, red=None, yellow=4, blue=5)
    # Mirrors the old `(x.drop_white or 0) + ...` expression: None -> 0.
    assert sum_drops(row) == 12


def test_sum_drops_all_set():
    row = _Row(white=1, black=2, red=3, yellow=4, blue=5)
    assert sum_drops(row) == 15


def test_sum_drops_strict_returns_none_when_any_channel_unset():
    row = _Row(white=1, black=2, red=None, yellow=4, blue=5)
    assert sum_drops_strict(row) is None


def test_sum_drops_strict_full_recipe():
    row = _Row(white=1, black=2, red=3, yellow=4, blue=5)
    assert sum_drops_strict(row) == 15


def test_prefix_supports_initial_drop_columns():
    row = _Row(prefix="initial_drop_", white=2, black=0, red=0, yellow=1, blue=0)
    assert sum_drops(row, prefix="initial_drop_") == 3
