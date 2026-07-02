#!/usr/bin/env python3
"""
Fill probe_schedule with the daily-challenge probe rotation.

The probe set (PROBE_COLOR_IDS) was selected from the v2 export on 2026-07-02:
mid-difficulty colours (recipe sum 8-13 drops), skin + lab mix, observed
P(perfect) between 0.33 and 0.53 with n >= 20 sessions each, so the daily
measurement neither floors nor ceilings:

  id 38  #392D1D              skin  sum 12  P(perfect) 0.33
  id 18  #C8AF91              skin  sum 11  P(perfect) 0.38
  id 25  #DE958F              skin  sum  9  P(perfect) 0.42
  id 47  Lab RGB(139,159,64)  lab   sum 10  P(perfect) 0.46
  id 58  Lab RGB(121,135,78)  lab   sum  8  P(perfect) 0.49

Rotation: the colours cycle in a fixed order, one per day, so every probe
colour returns every len(set) days and every user meets the same colour on
the same day. Idempotent: existing dates are left untouched, so re-running
extends the horizon without rewriting history.

Usage:
  # dry run, 90 days from today
  python3 scripts/generate_probe_schedule.py --days 90 --dry-run

  # write to the DB configured via DATABASE_URL / shadestudy.env
  python3 scripts/generate_probe_schedule.py --days 90

  # custom start date (e.g. extend from a known point)
  python3 scripts/generate_probe_schedule.py --start 2026-10-01 --days 90
"""
import argparse
from datetime import date, timedelta

from app import create_app, db
from app.models import ProbeSchedule, TargetColor

PROBE_COLOR_IDS = [38, 18, 25, 47, 58]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=90, help='Horizon length in days')
    ap.add_argument('--start', type=lambda s: date.fromisoformat(s), default=None,
                    help='First date (default: today)')
    ap.add_argument('--dry-run', action='store_true', help='Print the plan, write nothing')
    args = ap.parse_args()

    start = args.start or date.today()

    app = create_app()
    with app.app_context():
        missing = [cid for cid in PROBE_COLOR_IDS
                   if TargetColor.query.get(cid) is None]
        if missing:
            raise SystemExit(f'Probe colour ids not found in target_colors: {missing}')

        existing = {
            row.challenge_date
            for row in ProbeSchedule.query
            .filter(ProbeSchedule.challenge_date >= start,
                    ProbeSchedule.challenge_date < start + timedelta(days=args.days))
            .all()
        }

        # Cycle offset continues from the number of days already scheduled
        # anywhere, so extending the horizon does not restart the rotation.
        prior_total = ProbeSchedule.query.count()

        added = 0
        for i in range(args.days):
            d = start + timedelta(days=i)
            if d in existing:
                continue
            cid = PROBE_COLOR_IDS[(prior_total + added) % len(PROBE_COLOR_IDS)]
            cycle = (prior_total + added) // len(PROBE_COLOR_IDS) + 1
            if args.dry_run:
                print(f'{d}  colour {cid}  (cycle {cycle})')
            else:
                db.session.add(ProbeSchedule(
                    challenge_date=d, target_color_id=cid,
                    position=0, rotation_cycle=cycle,
                ))
            added += 1

        if args.dry_run:
            print(f'[dry-run] would add {added} rows '
                  f'({len(existing)} dates already scheduled in window)')
        else:
            db.session.commit()
            print(f'✅ added {added} probe_schedule rows from {start} '
                  f'({len(existing)} dates were already scheduled)')


if __name__ == '__main__':
    main()
