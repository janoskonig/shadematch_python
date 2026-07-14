#!/usr/bin/env python3
"""Sweep: mark every active match idle for > ABANDON_AFTER_DAYS as abandoned.

Protocol rule: "an active match not continued within 3 days is abandoned" —
the same rule is applied lazily whenever a player asks for their match
(app/matches.get_or_create_active_match); this sweep resolves matches of
players who never return, so no match stays statistically undecided.
Idempotent.

Production runs via the existing Render cron: the sweep is executed inside
POST /push/send-daily (before reminder copy is built) and is also exposed
standalone as POST /cron/mark-abandoned-matches (X-Cron-Secret guarded).
This CLI wrapper remains for ad-hoc/manual runs against any DB.

Usage:
    PYTHONPATH=. python3 scripts/mark_abandoned_matches.py [--commit]
"""
import argparse

from app import create_app, db
from app.matches import abandon_stale_matches, ABANDON_AFTER_DAYS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--commit', action='store_true', help='write (else dry-run rollback)')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        n = abandon_stale_matches()
        if args.commit:
            db.session.commit()
            print(f'✅ {n} match(es) abandoned (> {ABANDON_AFTER_DAYS} days idle).')
        else:
            db.session.rollback()
            print(f'dry-run: {n} match(es) WOULD be abandoned (> {ABANDON_AFTER_DAYS} days idle).')


if __name__ == '__main__':
    main()
