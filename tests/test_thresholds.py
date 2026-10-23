"""Known-answer checks for the cohort dashboards' PT/AT estimates."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.thresholds import summarize_judgments  # noqa: E402


def judgments(scale=1.0, shift=0.0):
    pairs = []
    for de, counts in [(0.3, (8, 2, 0)), (1.0, (6, 3, 1)),
                       (2.0, (2, 5, 3)), (4.0, (0, 2, 8))]:
        for judgment, n in zip(('identical', 'acceptable', 'unacceptable'), counts):
            pairs.extend([(de * scale + shift, judgment)] * n)
    return pairs


def test_recovers_independent_logistic_crossings():
    out = summarize_judgments(judgments())
    # Independent binomial-logistic GLM reference for these grouped counts.
    assert out['pt'] == {'value': 1.18, 'status': 'estimated'}
    assert out['at'] == {'value': 2.82, 'status': 'estimated'}
    assert out['n'] == 40
    assert out['counts'] == {'identical': 16, 'acceptable': 12, 'unacceptable': 12}
    scaled = summarize_judgments(judgments(scale=2.0))
    assert scaled['pt']['value'] == pytest.approx(2 * 1.18, abs=0.01)
    assert scaled['at']['value'] == pytest.approx(2 * 2.82, abs=0.01)


def test_invalid_judgments_and_distances_are_excluded():
    invalid = [(None, 'identical'), ('bad', 'acceptable'), (-1, 'unacceptable'),
               (float('nan'), 'identical'), (float('inf'), 'unacceptable'),
               (1, None), (2, 'stopped'), (3, 'perfect')]
    assert summarize_judgments(judgments() + invalid) == summarize_judgments(judgments())


def test_thresholds_can_be_available_independently():
    pairs = [(de, 'acceptable' if j == 'identical' else j) for de, j in judgments()]
    out = summarize_judgments(pairs)
    assert out['pt'] == {'value': None, 'status': 'one_response'}
    assert out['at'] == {'value': 2.82, 'status': 'estimated'}


@pytest.mark.parametrize('pairs,status', [
    ([], 'no_data'),
    ([(0, 'identical'), (2, 'unacceptable')], 'insufficient_data'),
    ([(1, 'identical')] * 6, 'one_response'),
    ([(1, 'identical'), (1, 'unacceptable')] * 3, 'no_delta_variation'),
    ([(1, 'unacceptable'), (4, 'identical')] * 10, 'no_increasing_fit'),
    ([(de, j) for de, n in [(1, 6), (2, 8), (3, 9)]
      for j in ['identical'] * (10 - n) + ['unacceptable'] * n], 'outside_range'),
])
def test_unsupported_thresholds_have_an_explicit_reason(pairs, status):
    out = summarize_judgments(pairs)
    assert out['pt'] == {'value': None, 'status': status}
    assert out['at'] == {'value': None, 'status': status}
