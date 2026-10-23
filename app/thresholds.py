"""Descriptive per-user PT/AT fits for the cohort dashboards.

Both sources use the calibration instrument's logistic 50% crossing. Game
judgments are self-selected stopping decisions, so their fit describes those
decisions rather than a controlled psychophysical measurement.
"""
import math

from .calibration import _logistic_threshold


JUDGMENTS = ('identical', 'acceptable', 'unacceptable')
MIN_JUDGMENTS = 6


def summarize_judgments(judgments):
    """Fit PT=P(difference)=.5 and AT=P(unacceptable)=.5 independently.

    Input is an iterable of (actual Delta E, judgment) pairs. Invalid values
    are excluded. Never report negative thresholds or extrapolate beyond the
    observed Delta E range; return a reason when a crossing is unavailable.
    """
    valid = []
    for delta_e, judgment in judgments:
        if judgment not in JUDGMENTS:
            continue
        try:
            delta_e = float(delta_e)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(delta_e) and delta_e >= 0:
            valid.append((delta_e, judgment))

    n = len(valid)
    x = [de for de, _ in valid]

    def fit(positive):
        result = {'value': None, 'status': 'no_data'}
        if not n:
            return result
        y = [int(judgment in positive) for _, judgment in valid]
        if n < MIN_JUDGMENTS:
            result['status'] = 'insufficient_data'
        elif min(y) == max(y):
            result['status'] = 'one_response'
        elif min(x) == max(x):
            result['status'] = 'no_delta_variation'
        else:
            fitted = _logistic_threshold(x, y)
            if fitted is None:
                result['status'] = 'no_increasing_fit'
            elif not all(math.isfinite(v) for v in fitted):
                result['status'] = 'unstable_fit'
            elif not min(x) <= fitted[0] <= max(x):
                result['status'] = 'outside_range'
            else:
                result.update(value=round(fitted[0], 2), status='estimated')
        return result

    return {
        'n': n,
        'counts': {j: sum(judgment == j for _, judgment in valid) for j in JUDGMENTS},
        'delta_e_min': min(x) if n else None,
        'delta_e_max': max(x) if n else None,
        'pt': fit({'acceptable', 'unacceptable'}),
        'at': fit({'unacceptable'}),
    }
