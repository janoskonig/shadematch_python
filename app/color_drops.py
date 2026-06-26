"""Canonical helpers for the five paint channels.

The mixing palette has exactly five paints (white, black, red, yellow, blue).
Their per-attempt *drop counts* are stored — denormalised — across several
tables (``TargetColor.drop_*``, ``MixingSession.drop_*`` and
``MixingAttempt.initial_drop_*``) and are summed/iterated in many places across
``routes.py``, ``gamification.py`` and ``next_action.py``.

Historically each call site re-listed the five channel names and re-implemented
the summation inline, with two subtly different conventions:

* a *lenient* sum that treats a missing/``None`` channel as ``0`` (used for
  step-count fallbacks on session/attempt rows), and
* a *strict* sum that returns ``None`` when any channel is unset, so that a
  partially specified catalog recipe is treated as "no recipe" rather than a
  smaller number (used for ``TargetColor`` recipes).

This module is the single source of truth for the channel names and for both
conventions, so the logic lives in one place instead of being copy-pasted. The
behaviour is identical to the previous inline implementations.
"""

# Authoritative ordering of the five paint channels. Import this anywhere the
# palette needs to be enumerated (validation, serialisation, summation) instead
# of re-spelling the tuple.
PAINT_CHANNELS = ('white', 'black', 'red', 'yellow', 'blue')


def _channel_values(obj, prefix='drop_'):
    """Return the five channel values from ``obj`` in :data:`PAINT_CHANNELS` order.

    ``prefix`` selects the column family, e.g. ``'drop_'`` for ``TargetColor`` /
    ``MixingSession`` rows or ``'initial_drop_'`` for ``MixingAttempt`` rows.
    Missing attributes are reported as ``None``.
    """
    return [getattr(obj, prefix + name, None) for name in PAINT_CHANNELS]


def sum_drops(obj, prefix='drop_'):
    """Total drops across all five channels, treating ``None`` as ``0``.

    Equivalent to the previous ``(x.drop_white or 0) + ...`` expressions.
    """
    return sum(int(v or 0) for v in _channel_values(obj, prefix))


def sum_drops_strict(obj, prefix='drop_'):
    """Total recipe drops, or ``None`` if any channel is unset.

    A partially specified recipe (any channel ``None``) is treated as "no
    recipe". Equivalent to the previous ``target_color_sum_drop`` body.
    """
    vals = _channel_values(obj, prefix)
    if any(v is None for v in vals):
        return None
    return int(sum(int(v or 0) for v in vals))
