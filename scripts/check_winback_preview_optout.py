#!/usr/bin/env python3
"""
READ-ONLY forensic check: did a win-back preview opt somebody out by accident?

Until it was fixed, `send_winback.py --test-to <you>` mailed the preview under
the SAMPLED participant's user id, so the one-click unsubscribe link in YOUR
inbox carried THEIR token. One click on it would opt that participant out —
silently, and without them ever asking.

READ THIS BEFORE BELIEVING THE OUTPUT. The database CANNOT answer the question:

  * `email_opt_in_reminders` defaults to False, and /register fills it from a
    checkbox that is unticked by default. So "reminders off" means EITHER
    "never asked for reminders" OR "asked, then unsubscribed" — the two are
    indistinguishable in the column. There is no unsubscribe timestamp and no
    event log.
  * Typically ~30% of lapsed candidates simply never ticked the box. At that
    base rate, the top-ranked candidate having reminders off is an ordinary
    coincidence, not evidence.

So this script does not accuse anyone. It lists the candidates, shows who
actually received a win-back, and reports the base rate so a single "reminders
off" row cannot be mistaken for a finding.

GROUND TRUTH LIVES IN YOUR INBOX, NOT THE DB: every signup sends an admin
notification containing "Reminders opt-in: yes/no" as of registration (see
send_new_user_admin_email in /register). Search the admin mailbox for the user
id. If it says "no", they never opted in and there is nothing to chase. If it
says "yes" and the column is now False, then something turned it off — and the
preview bug is a real candidate for what.

This script only SELECTs. It never writes, and it sends no mail.

Usage:
  python scripts/check_winback_preview_optout.py
  python scripts/check_winback_preview_optout.py --top 10

Pass the same eligibility flags you used for the campaign if you changed them
from the defaults, or the reconstruction will not match what the script saw.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sqlalchemy import text as sql_text  # noqa: E402

from app import create_app  # noqa: E402
from app.models import db  # noqa: E402

SENT_LOG = os.path.join(REPO_ROOT, 'artifacts', 'winback', 'sent_log.csv')


# send_winback.CANDIDATE_SQL with ONE change: the opt-in filter is dropped and
# selected instead, so an opted-out would-be candidate still shows up. Everything
# else — the fondness test, the lapse test, the ordering — is identical, because
# the ordering is what decides who the preview modelled.
RECONSTRUCT_SQL = sql_text(
    """
    WITH play AS (
        SELECT user_id,
               COUNT(*)                        AS attempts,
               COUNT(DISTINCT date(timestamp)) AS active_days,
               MAX(timestamp)                  AS last_play
        FROM mixing_sessions
        WHERE timestamp IS NOT NULL
        GROUP BY user_id
    )
    SELECT u.id                               AS user_id,
           u.email                            AS email,
           u.nickname                         AS nickname,
           u.email_opt_in_reminders           AS opted_in,
           p.attempts                         AS attempts,
           p.active_days                      AS active_days,
           date(p.last_play)                  AS last_play,
           (CURRENT_DATE - date(p.last_play)) AS days_since_last,
           COALESCE(up.longest_streak, 0)     AS longest_streak
    FROM users u
    JOIN play p                ON p.user_id = u.id
    LEFT JOIN user_progress up ON up.user_id = u.id
    WHERE u.email IS NOT NULL
      AND u.email_verified_at IS NOT NULL
      AND (CURRENT_DATE - date(p.last_play)) >= :lapse_days
      AND (
            p.active_days >= :min_days
         OR p.attempts    >= :min_attempts
         OR COALESCE(up.longest_streak, 0) >= :min_streak
      )
    ORDER BY p.active_days DESC, p.attempts DESC
    """
)


def load_sent_log():
    """user_ids that actually received a real win-back."""
    if not os.path.exists(SENT_LOG):
        return set(), False
    with open(SENT_LOG, newline='') as f:
        return {r['user_id'] for r in csv.DictReader(f) if r.get('user_id')}, True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--lapse-days', type=int, default=7)
    ap.add_argument('--min-days', type=int, default=3)
    ap.add_argument('--min-attempts', type=int, default=20)
    ap.add_argument('--min-streak', type=int, default=3)
    ap.add_argument('--top', type=int, default=8,
                    help='How many of the top candidates to show (default 8).')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        print(f"DB: {uri.split('@')[-1][:60]}")
        print("READ-ONLY: this script only SELECTs.\n")

        rows = [dict(r) for r in db.session.execute(RECONSTRUCT_SQL, {
            'lapse_days': args.lapse_days,
            'min_days': args.min_days,
            'min_attempts': args.min_attempts,
            'min_streak': args.min_streak,
        }).mappings().all()]
        sent, have_log = load_sent_log()

        if not have_log:
            print(f"No sent-log at {SENT_LOG} — nothing to cross-reference.")
            return 1
        if not rows:
            print("No win-back candidates reconstruct at all. Check the flags "
                  "match the campaign you ran.")
            return 1

        print(f"Reconstructed candidates (opt-in filter removed): {len(rows)}")
        print(f"Real win-backs recorded in the sent-log: {len(sent)}\n")

        print(f"  {'rank':>4}  {'user':6} {'act_days':>8} {'attempts':>8} {'streak':>6} "
              f"{'reminders':>9} {'got_winback':>11}")
        for i, r in enumerate(rows[:args.top], start=1):
            got = r['user_id'] in sent
            on = bool(r['opted_in'])
            print(f"  {i:>4}  {r['user_id']:6} {r['active_days']:>8} {r['attempts']:>8} "
                  f"{r['longest_streak']:>6} {('on' if on else 'OFF'):>9} "
                  f"{('yes' if got else 'no'):>11}")

        off = [r for r in rows if not r['opted_in']]
        off_and_sent = [r for r in off if r['user_id'] in sent]
        rate = (100.0 * len(off) / len(rows)) if rows else 0.0

        print()
        print(f"Reminders OFF among candidates : {len(off)}/{len(rows)} ({rate:.0f}%)")
        print(f"  ...of whom received a win-back: {len(off_and_sent)}  "
              "(these turned it off AFTER reading it — an ordinary unsubscribe)")
        print()
        print(f"BASE RATE CAVEAT: {rate:.0f}% of your lapsed candidates have reminders off,")
        print("and most of those never ticked the box at signup. At that rate, any single")
        print("row being off — including rank 1 — is what you would expect by chance.")
        print("This listing is NOT evidence that the preview bug opted anyone out.")
        print()
        print("To actually settle it, check the ground truth in your admin mailbox:")
        for i, r in enumerate(rows[:args.top], start=1):
            if not r['opted_in'] and r['user_id'] not in sent:
                print(f"  search for \"{r['user_id']}\" in the new-signup notifications"
                      f"{' (rank 1 — the slot the preview sampled)' if i == 1 else ''}")
        print("  Each one records 'Reminders opt-in: yes/no' as of registration.")
        print("    says 'no'  -> they never opted in. Nothing happened. Leave them alone.")
        print("    says 'yes' -> something turned it off. Then it is worth a human email.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
