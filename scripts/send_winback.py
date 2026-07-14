#!/usr/bin/env python3
"""
Win-back campaign: re-engage players who were *fond* of ShadeMatch but have
gone quiet.

Eligibility (all three must hold):
  1. Reachable & consented — email present, email_opt_in_reminders=True,
     email_verified_at set (identical gate to the daily reminder / announcement
     paths). We never contact anyone who did not opt into reminders.
  2. Was fond of the game — played on >= --min-days distinct days, OR logged
     >= --min-attempts attempts, OR reached a longest_streak >= --min-streak.
  3. Has lapsed — last mixing attempt was >= --lapse-days ago.

Copy is distinct from the daily nudge: it references what the player achieved
(colors mastered, longest streak, days away) and invites them to pick up where
they left off. Rendered through the existing ``daily_reminder`` email template
(via email_utils.send_daily_reminder_email), in the RECIPIENT's locale, with
one-click unsubscribe (RFC 8058) and bulk headers.

A durable sent-log (artifacts/winback/sent_log.csv) records every real send so
re-runs skip anyone contacted within --cooldown-days.

SAFE BY DEFAULT: with no flags this is a DRY RUN — it lists who would be
contacted and previews the copy, and sends nothing. You must pass --send to
actually deliver.

Usage:
  python scripts/send_winback.py                      # DRY RUN (default) — list + preview, send nothing
  python scripts/send_winback.py --test-to a@b.com    # send ONE preview to that address
  python scripts/send_winback.py --send               # send to all eligible, not-recently-contacted users
  python scripts/send_winback.py --send --limit 25    # send at most 25 this run

Loads DATABASE_URL / SMTP config from shadestudy.env via app.create_app.
Run it yourself (it reads/writes the production DB and sends real email).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sqlalchemy import text as sql_text  # noqa: E402

from app import create_app, email_utils  # noqa: E402
from app.models import User, db  # noqa: E402
from app.i18n import t_for  # noqa: E402

SENT_LOG = os.path.join(REPO_ROOT, 'artifacts', 'winback', 'sent_log.csv')


# ── Candidate selection ────────────────────────────────────────────────────

CANDIDATE_SQL = sql_text(
    """
    WITH play AS (
        SELECT user_id,
               COUNT(*)                        AS attempts,
               COUNT(DISTINCT date(timestamp)) AS active_days,
               MAX(timestamp)                  AS last_play
        FROM mixing_sessions
        WHERE timestamp IS NOT NULL
        GROUP BY user_id
    ),
    mastered AS (
        SELECT user_id, COUNT(*) AS mastered_colors
        FROM user_target_color_stats
        WHERE completed_count > 0
        GROUP BY user_id
    )
    SELECT u.id                                    AS user_id,
           u.email                                 AS email,
           u.locale                                AS locale,
           p.attempts                              AS attempts,
           p.active_days                           AS active_days,
           date(p.last_play)                       AS last_play,
           (CURRENT_DATE - date(p.last_play))      AS days_since_last,
           COALESCE(up.longest_streak, 0)          AS longest_streak,
           COALESCE(m.mastered_colors, 0)          AS mastered_colors
    FROM users u
    JOIN play p              ON p.user_id = u.id
    LEFT JOIN user_progress up ON up.user_id = u.id
    LEFT JOIN mastered m       ON m.user_id = u.id
    WHERE u.email IS NOT NULL
      AND u.email_opt_in_reminders IS TRUE
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


def candidates(*, lapse_days, min_days, min_attempts, min_streak):
    rows = db.session.execute(
        CANDIDATE_SQL,
        {
            'lapse_days': lapse_days,
            'min_days': min_days,
            'min_attempts': min_attempts,
            'min_streak': min_streak,
        },
    ).mappings().all()
    return [dict(r) for r in rows]


# ── Win-back copy (rendered in the recipient's locale) ─────────────────────

def build_context(row) -> dict:
    """Build the daily_reminder template context with win-back copy."""
    loc = row.get('locale') or 'en'
    uid = row['user_id']
    away = int(row['days_since_last'])
    longest = int(row['longest_streak'])
    mastered = int(row['mastered_colors'])

    if mastered > 0 and longest >= 2:
        subhead = t_for(
            loc,
            "You mastered {mastered} colors and reached a {longest}-day streak "
            "before stepping away {away} days ago. One quick mix picks up right "
            "where you left off.",
            mastered=mastered, longest=longest, away=away,
        )
    elif longest >= 2:
        subhead = t_for(
            loc,
            "You were on a {longest}-day streak before stepping away {away} days "
            "ago. One quick mix picks up right where you left off.",
            longest=longest, away=away,
        )
    else:
        subhead = t_for(
            loc,
            "It's been {away} days since your last mix. Pick a shade and jump "
            "back in — it only takes a minute.",
            away=away,
        )

    stats = []
    if mastered > 0:
        stats.append({'label': t_for(loc, 'Colors mastered'), 'value': str(mastered)})
    if longest > 0:
        stats.append({'label': t_for(loc, 'Longest streak'), 'value': str(longest)})
    stats.append({'label': t_for(loc, 'Days away'), 'value': str(away)})

    return {
        'subject': t_for(loc, 'Your ShadeMatch colors are waiting'),
        'eyebrow': t_for(loc, 'We kept your palette warm'),
        'headline': t_for(loc, 'Your colors miss you, {user_id}', user_id=uid),
        'subhead': subhead,
        'preheader': t_for(loc, 'Pick up right where you left off — one quick mix.'),
        'cta_url': email_utils.base_url() + '/',
        'cta_label': t_for(loc, 'Jump back in →'),
        'stats': stats,
        'tip': t_for(loc, 'No streak pressure — even a single 30-second mix counts.'),
    }


def send_one(row):
    ctx = build_context(row)
    email_utils.send_daily_reminder_email(
        to_email=row['email'],
        user_id=row['user_id'],
        context=ctx,
        locale=(row.get('locale') or 'en'),
    )


# ── Sent-log (durable cooldown / de-dup) ───────────────────────────────────

def load_recent_sent(cooldown_days):
    """Return the set of user_ids contacted within the cooldown window."""
    if not os.path.exists(SENT_LOG):
        return set()
    cutoff = date.today() - timedelta(days=cooldown_days)
    recent = set()
    with open(SENT_LOG, newline='') as f:
        for r in csv.DictReader(f):
            try:
                sent_on = datetime.fromisoformat(r['sent_at']).date()
            except (KeyError, ValueError):
                continue
            if sent_on >= cutoff:
                recent.add(r['user_id'])
    return recent


def append_sent_log(user_id, sent_at_iso):
    os.makedirs(os.path.dirname(SENT_LOG), exist_ok=True)
    is_new = not os.path.exists(SENT_LOG)
    with open(SENT_LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(['user_id', 'sent_at'])
        w.writerow([user_id, sent_at_iso])


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--send', action='store_true',
                    help='Actually send. Without this flag the script is a DRY RUN.')
    ap.add_argument('--test-to', help='Send a single preview to this address (uses the first eligible user for copy).')
    ap.add_argument('--lapse-days', type=int, default=7, help='Min days since last play to count as lapsed (default 7).')
    ap.add_argument('--min-days', type=int, default=3, help='Distinct active days to count as fond (default 3).')
    ap.add_argument('--min-attempts', type=int, default=20, help='Attempts to count as fond (default 20).')
    ap.add_argument('--min-streak', type=int, default=3, help='Longest streak to count as fond (default 3).')
    ap.add_argument('--cooldown-days', type=int, default=45, help='Skip users contacted within this many days (default 45).')
    ap.add_argument('--limit', type=int, default=0, help='Cap the number of sends this run (0 = no cap).')
    ap.add_argument('--sleep', type=float, default=0.4, help='Seconds between sends.')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        rows = candidates(
            lapse_days=args.lapse_days,
            min_days=args.min_days,
            min_attempts=args.min_attempts,
            min_streak=args.min_streak,
        )
        recent = load_recent_sent(args.cooldown_days)
        fresh = [r for r in rows if r['user_id'] not in recent]
        skipped_cooldown = len(rows) - len(fresh)

        print(f"Eligible (fond + lapsed + opted-in verified): {len(rows)}")
        print(f"  skipped (contacted within {args.cooldown_days}d): {skipped_cooldown}")
        print(f"  to contact this run: {len(fresh)}"
              + (f" (capped at {args.limit})" if args.limit and len(fresh) > args.limit else ""))
        print()
        print(f"  {'user':6} {'days_away':>9} {'act_days':>8} {'attempts':>8} {'streak':>6} {'mastered':>8}  locale  email")
        for r in fresh:
            print(f"  {r['user_id']:6} {r['days_since_last']:>9} {r['active_days']:>8} "
                  f"{r['attempts']:>8} {r['longest_streak']:>6} {r['mastered_colors']:>8}  "
                  f"{(r.get('locale') or 'en'):6}  {r['email']}")

        if args.test_to:
            sample = fresh[0] if fresh else (rows[0] if rows else None)
            if sample is None:
                print("\nNo eligible user to build preview copy from.")
                return 1
            preview = dict(sample)
            preview['email'] = args.test_to
            send_one(preview)
            print(f"\nTest win-back sent to {args.test_to} (copy modeled on user {sample['user_id']}).")
            return 0

        if not args.send:
            print("\nDRY RUN — nothing sent. Re-run with --send to deliver.")
            if fresh:
                ctx = build_context(fresh[0])
                print("\nPreview (first recipient, their locale):")
                print(f"  subject:  {ctx['subject']}")
                print(f"  headline: {ctx['headline']}")
                print(f"  subhead:  {ctx['subhead']}")
            return 0

        batch = fresh[:args.limit] if args.limit else fresh
        sent = failed = 0
        for r in batch:
            try:
                send_one(r)
                append_sent_log(r['user_id'], datetime.utcnow().isoformat())
                sent += 1
                print(f"  sent   -> {r['user_id']} {r['email']}")
            except Exception as exc:  # noqa: BLE001 — report and continue
                failed += 1
                print(f"  FAILED -> {r['user_id']} {r['email']}: {exc}")
            time.sleep(args.sleep)
        print(f"\nDone. sent={sent} failed={failed}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
