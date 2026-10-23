"""Known-answer checks for lifetime, daily and early/recent performance."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import hetfo  # noqa: E402


def row(de=1.0, seconds=60, category='acceptable_difference', uid='USER01',
        stamp=datetime(2026, 8, 31, 10)):
    return SimpleNamespace(user_id=uid, timestamp=stamp, delta_e=de,
                           time_sec=seconds, match_category=category)


def test_summary_includes_skips_and_keeps_metric_denominators_separate():
    rows = [row(0, 0, 'perfect'), row(2, 4000, 'acceptable_difference'),
            row(8, None, 'big_difference'), row(None, 120, 'stopped')]
    assert hetfo._performance_summary(rows) == {
        'rounds': 4, 'completed': 2, 'perfect': 1, 'completion_rate': 50,
        'delta_e_n': 3, 'mean_delta_e': 3.33, 'median_delta_e': 2,
        'time_n': 3, 'median_time_sec': 120, 'play_time_sec': 1920,
    }


def test_invalid_values_are_missing_instead_of_zero():
    rows = [row(de, seconds) for de, seconds in [
        (None, None), (-1, -2), (float('nan'), float('inf')), (0, 0),
    ]]
    out = hetfo._performance_summary(rows)
    assert out['rounds'] == 4
    assert out['delta_e_n'] == out['time_n'] == 1
    assert out['median_delta_e'] == out['median_time_sec'] == 0
    assert hetfo._performance_summary([row(None, None)])['median_delta_e'] is None
    assert hetfo._performance_summary([])['completion_rate'] is None


def test_daily_history_uses_local_dates_and_does_not_fill_inactive_days():
    rows = [row(3, stamp=datetime(2026, 8, 31, 21, 59)),
            row(1, stamp=datetime(2026, 8, 31, 22)),
            row(0, stamp=datetime(2026, 9, 4, 10))]
    out = hetfo._performance_by_user(rows, hetfo.cohort_tz())['USER01']
    assert [p['day'] for p in out['daily']] == ['2026-08-31', '2026-09-01', '2026-09-04']
    assert [p['cumulative_rounds'] for p in out['daily']] == [1, 2, 3]
    assert out['active_days'] == 3
    assert out['first_day'] == '2026-08-31' and out['last_day'] == '2026-09-04'


def test_undated_rows_count_in_totals_without_inventing_a_date():
    rows = [row(99, stamp=None), row(2), row(0, stamp=datetime(2026, 9, 1, 10))]
    out = hetfo._performance_by_user(rows, hetfo.cohort_tz())['USER01']
    assert out['overall']['rounds'] == 3 and out['undated_rounds'] == 1
    assert sum(p['rounds'] for p in out['daily']) == 2
    assert out['comparison']['first']['median_delta_e'] == 2
    assert out['comparison']['latest']['median_delta_e'] == 0
    only_undated = hetfo._performance_by_user([row(stamp=None)], hetfo.cohort_tz())['USER01']
    assert only_undated['first_day'] is None and only_undated['daily'] == []
    assert only_undated['comparison'] is None


@pytest.mark.parametrize('n', [1, 2, 3, 19, 20, 21, 25])
def test_early_recent_windows_have_equal_sizes_and_never_overlap(n):
    rows = [row(i, stamp=datetime(2026, 8, 31, 10) + timedelta(minutes=i))
            for i in range(n)]
    out = hetfo._performance_by_user(rows, hetfo.cohort_tz())['USER01']
    if n == 1:
        assert out['comparison'] is None
        return
    comparison = out['comparison']
    k = min(10, n // 2)
    assert comparison['window_rounds'] == k
    assert comparison['first']['rounds'] == comparison['latest']['rounds'] == k
    assert comparison['first']['median_delta_e'] == (k - 1) / 2
    assert comparison['latest']['median_delta_e'] == n - (k + 1) / 2


def test_user_histories_do_not_mix():
    out = hetfo._performance_by_user([row(1), row(8, uid='USER02')], hetfo.cohort_tz())
    assert out['USER01']['overall']['median_delta_e'] == 1
    assert out['USER02']['overall']['median_delta_e'] == 8
