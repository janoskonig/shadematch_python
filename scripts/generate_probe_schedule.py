#!/usr/bin/env python3
"""Fill probe_schedule with the daily-challenge probe rotation — region-based.

The daily channel's value is that *everyone plays the same colour on the same day*, so it
yields a clean population-level learning curve. Instead of returning the same exact colour
(which confounds learning with recipe memorisation), we rotate over a small set of gamut
colour-space REGIONS and serve a *different* colour from a region on each of its returns —
so the curve measures regional transfer, not recall.

Probe regions: N multi-colour gamut regions, split between the Xiao skin zone (the study's
focus) and mid-difficulty background regions. Rotation: day i → region[i % N]; the k-th
return of a region serves that region's k-th colour (wrapping only once its colours are
exhausted). The region id is stored in `notes` for the analysis.

Run:  python3 scripts/generate_probe_schedule.py --env shadestudy.env [--days 42] [--commit]
"""
import argparse
import os
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

N_SKIN = 3          # skin-zone probe regions
N_BG = 3            # mid-difficulty background probe regions
MIN_COLOURS = 4     # a probe region needs at least this many colours
BG_BAND_LO, BG_BAND_HI = 12, 20   # "mid-difficulty" band window for background regions


def load_env(env_path):
    for line in Path(env_path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--env', required=True)
    ap.add_argument('--days', type=int, default=42, help='Horizon length in days')
    ap.add_argument('--start', type=lambda s: date.fromisoformat(s), default=None)
    ap.add_argument('--commit', action='store_true', help='Clear + write (else dry-run)')
    args = ap.parse_args()

    load_env(REPO / args.env)
    if not os.environ.get('DATABASE_URL'):
        raise SystemExit('DATABASE_URL not set')

    from app import create_app, db
    from app.models import ProbeSchedule, TargetColor
    from app.gamification import target_color_sum_drop
    from app.regions import region_of_target

    start = args.start or date.today()
    app = create_app()
    with app.app_context():
        gamut = TargetColor.query.filter_by(color_type='gamut').order_by(
            TargetColor.catalog_order.asc()).all()
        by_region = defaultdict(list)
        for tc in gamut:
            by_region[region_of_target(tc)].append(tc)
        multi = {r: cs for r, cs in by_region.items() if len(cs) >= MIN_COLOURS}

        def med_band(cs):
            return statistics.median(target_color_sum_drop(c) for c in cs)

        skin = sorted((r for r in multi if r.startswith('skin:')),
                      key=lambda r: (-len(multi[r]), r))[:N_SKIN]
        bg = sorted((r for r in multi if r.startswith('bg:')
                     and BG_BAND_LO <= med_band(multi[r]) <= BG_BAND_HI),
                    key=lambda r: (-len(multi[r]), r))[:N_BG]
        # interleave skin / bg so consecutive days alternate zone
        regions = [r for pair in zip(skin, bg) for r in pair]
        regions += skin[len(bg):] + bg[len(skin):]
        if len(regions) < 2:
            raise SystemExit('Not enough multi-colour probe regions found.')

        colours = {r: [c.id for c in multi[r]] for r in regions}
        print(f'probe regions ({len(regions)}):')
        for r in regions:
            print(f'  {r:16s} band~{med_band(multi[r]):.0f}  {len(colours[r])} colours')

        # Build the rotation.
        plan = []
        returns = defaultdict(int)
        for i in range(args.days):
            d = start + timedelta(days=i)
            r = regions[i % len(regions)]
            k = returns[r]
            cid = colours[r][k % len(colours[r])]
            wrapped = k >= len(colours[r])
            plan.append((d, cid, r, k + 1, wrapped))
            returns[r] += 1

        wraps = sum(1 for *_, w in plan if w)
        print(f'\nhorizon {args.days} days | {wraps} day(s) reuse a colour (region exhausted)')
        for d, cid, r, cyc, w in plan[:8]:
            print(f'  {d}  colour {cid}  {r}  cycle {cyc}{"  (reuse)" if w else ""}')

        if not args.commit:
            print('\nDRY-RUN: re-run with --commit to clear old rows and write.')
            return

        deleted = ProbeSchedule.query.filter(ProbeSchedule.challenge_date >= start).delete()
        for d, cid, r, cyc, _w in plan:
            db.session.add(ProbeSchedule(challenge_date=d, target_color_id=cid,
                                         position=0, rotation_cycle=cyc, notes=r))
        db.session.commit()
        print(f'\ncleared {deleted} old rows (>= {start}); wrote {len(plan)} region-based rows.')


if __name__ == '__main__':
    main()
