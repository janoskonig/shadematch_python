#!/usr/bin/env python3
"""
Challenge echo: tell a player that someone took the challenge they sent.

The challenge history on /results is pull-only, so a creator whose link was
played never finds out unless they go looking. The in-app banner (see
build_challenge_echo in app/routes.py) covers people who open the app; this
covers the ones who do not. Same event, same window, same wording — this script
calls build_challenge_echo itself rather than re-deriving it, so the email can
never disagree with the banner.

Eligibility (all must hold):
  1. Reachable & consented — email present, email_opt_in_reminders=True,
     email_verified_at set (identical gate to the daily reminder / win-back
     paths). We never contact anyone who did not opt into reminders.
  2. Someone accepted one of their challenges inside the echo window
     (CHALLENGE_ECHO_WINDOW_DAYS, shared with the banner).
  3. That acceptance is newer than the last one we told them about — the
     sent-log stores the echo's `latest_at` watermark, mirroring the client's
     localStorage seen-marker. No watermark bump, no email.
  4. Not contacted within --cooldown-days, so a popular challenge cannot turn
     into a stream of mail.

Copy is bilingual (Hungarian first, English mirror), rendered through the
existing ``daily_reminder`` template via email_utils.send_daily_reminder_email,
with one-click unsubscribe (RFC 8058) and bulk headers. The CTA points at the
same place as the in-app banner: the challenge history on /results.

SAFE BY DEFAULT: with no flags this is a DRY RUN — it lists who would be
contacted and previews the copy, and sends nothing. You must pass --send to
actually deliver.

Usage:
  python scripts/send_challenge_echo.py                    # DRY RUN — list + preview, send nothing
  python scripts/send_challenge_echo.py --test-to a@b.com  # send ONE preview to that address
  python scripts/send_challenge_echo.py --send             # send to everyone eligible
  python scripts/send_challenge_echo.py --send --limit 25  # send at most 25 this run

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
from app.models import db  # noqa: E402
from app.routes import (  # noqa: E402
    CHALLENGE_ECHO_WINDOW_DAYS,
    build_challenge_echo,
)

SENT_LOG = os.path.join(REPO_ROOT, 'artifacts', 'challenge_echo', 'sent_log.csv')


# ── Candidate selection ────────────────────────────────────────────────────

# Cheap pre-filter: reachable creators whose challenge was accepted inside the
# window. build_challenge_echo then does the real work per user (count, best
# beat, watermark) so the email and the banner cannot drift apart.
CANDIDATE_SQL = sql_text(
    """
    SELECT DISTINCT u.id       AS user_id,
                    u.email    AS email,
                    u.nickname AS nickname,
                    u.locale   AS locale
    FROM users u
    JOIN challenge_links cl    ON cl.creator_user_id = u.id
    JOIN challenge_attempts ca ON ca.challenge_code = cl.code
    WHERE u.email IS NOT NULL
      AND u.email_opt_in_reminders IS TRUE
      AND u.email_verified_at IS NOT NULL
      AND ca.created_at >= :since
    ORDER BY u.id
    """
)


def candidates():
    since = datetime.utcnow() - timedelta(days=CHALLENGE_ECHO_WINDOW_DAYS)
    rows = db.session.execute(CANDIDATE_SQL, {'since': since}).mappings().all()
    out = []
    for r in rows:
        echo = build_challenge_echo(r['user_id']).get('challenge_echo')
        if not echo:
            continue
        row = dict(r)
        row['echo'] = echo
        out.append(row)
    return out


# ── Echo copy (bilingual: Hungarian first, English mirror below) ───────────

def build_context(row) -> dict:
    """Build the daily_reminder template context with HU+EN echo copy.

    Every recipient gets both languages in one email (HU paragraph, then the
    EN mirror as ``subhead2``; short fields carry both joined with " · ").
    The ``locale`` passed to the template only affects the fixed chrome
    (footer, Tip label), not this copy.
    """
    echo = row['echo']
    count = int(echo['count'])
    beat = echo.get('best_beat')
    name = (row.get('nickname') or '').strip()

    if beat:
        de = f"{float(beat['delta_e']):.2f}"
        # 'user' is localized for whatever locale the app context had; word the
        # guest case ourselves so both languages read naturally.
        rival_hu = 'Egy vendég' if beat['is_guest'] else str(beat['user'])
        rival_en = 'A guest' if beat['is_guest'] else str(beat['user'])
        hu = (
            f"{rival_hu} elfogadta a kihívásod, és pontosabban kevert ki nálad: "
            f"ΔE {de}. A színed és mindkettőtök keveréke ott van egymás mellett — "
            "nézd meg, hol csúszott el, és vágj vissza."
        )
        en = (
            f"{rival_en} took your challenge and mixed it closer than you did: "
            f"ΔE {de}. Your shade and both mixes are side by side — see where it "
            "slipped, and take it back."
        )
        subject = (f'{rival_hu} megverte a kihívásod (ΔE {de}) · '
                   f'{rival_en} beat your challenge (ΔE {de})')
        eyebrow = 'Megverték a kihívásod · Your challenge was beaten'
        headline = (f'{rival_hu} pontosabb volt · {rival_en} got closer')
        preheader = (f'ΔE {de} — szorosabb, mint a tiéd. · '
                     f'ΔE {de} — closer than yours.')
        tip = ('A visszavágó egy keverés. Az eredmény ott vár a kihívás-történetedben. · '
               'A rematch is one mix away. The result is waiting in your challenge history.')
    else:
        if count == 1:
            hu = ("Valaki elfogadta a kihívásod, és megpróbálta kikeverni a színed — "
                  "de nem sikerült megvernie. A címed egyelőre áll.")
            en = ("Someone took your challenge and tried to match your shade — and "
                  "did not beat you. Your title stands, for now.")
            subject = ('Valaki lejátszotta a kihívásod · Someone played your challenge')
        else:
            hu = (f"{count}-an fogadták el a kihívásod, és próbálták kikeverni a "
                  "színed — de egyikük sem vert meg. A címed egyelőre áll.")
            en = (f"{count} people took your challenge and tried to match your shade "
                  "— and none of them beat you. Your title stands, for now.")
            subject = (f'{count}-an próbálták a kihívásod · '
                       f'{count} people tried your challenge')
        eyebrow = 'Elfogadták a kihívásod · Your challenge was taken'
        headline = 'A címed áll · Your title stands'
        preheader = ('Megpróbálták, nem sikerült. · They tried; they did not beat you.')
        tip = ('Minden elfogadott kihívás egy emberrel több, aki színt kever. Köszönjük. · '
               'Every challenge taken is one more person mixing colour. Thank you.')

    stats = [{'label': 'Elfogadások · Times taken', 'value': str(count)}]
    if beat:
        stats.append({'label': 'A legjobb ΔE · Best ΔE',
                      'value': f"{float(beat['delta_e']):.2f}"})

    greeting = f'{headline}, {name}!' if name else headline
    return {
        'subject': subject,
        'eyebrow': eyebrow,
        'headline': greeting,
        'subhead': hu,
        'subhead2': en,
        'preheader': preheader,
        'cta_url': email_utils.base_url() + '/results#challenges-section',
        'cta_label': 'Megnézem → · See the result →',
        'stats': stats,
        'tip': tip,
        # This is not the daily nudge: swap the header badge and drop the
        # streak footer, both of which the daily_reminder template defaults to.
        'header_badge': 'Kihívás · Challenge',
        'footer_note': '',
    }


def send_one(row):
    ctx = build_context(row)
    email_utils.send_daily_reminder_email(
        to_email=row['email'],
        user_id=row['user_id'],
        context=ctx,
        locale=(row.get('locale') or 'en'),
    )


# ── Sent-log (watermark + cooldown) ────────────────────────────────────────

def load_sent_state(cooldown_days):
    """Return (watermark_by_user, recently_contacted).

    watermark_by_user maps user_id -> the newest echo `latest_at` we have
    already emailed them about, so a re-run with no new acceptance sends
    nothing. recently_contacted is the cooldown set.
    """
    watermark = {}
    recent = set()
    if not os.path.exists(SENT_LOG):
        return watermark, recent
    cutoff = date.today() - timedelta(days=cooldown_days)
    with open(SENT_LOG, newline='') as f:
        for r in csv.DictReader(f):
            uid = r.get('user_id')
            if not uid:
                continue
            seen = r.get('latest_at') or ''
            if seen > watermark.get(uid, ''):
                watermark[uid] = seen
            try:
                sent_on = datetime.fromisoformat(r['sent_at']).date()
            except (KeyError, TypeError, ValueError):
                continue
            if sent_on >= cutoff:
                recent.add(uid)
    return watermark, recent


def append_sent_log(user_id, sent_at_iso, latest_at):
    os.makedirs(os.path.dirname(SENT_LOG), exist_ok=True)
    is_new = not os.path.exists(SENT_LOG)
    with open(SENT_LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(['user_id', 'sent_at', 'latest_at'])
        w.writerow([user_id, sent_at_iso, latest_at or ''])


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--send', action='store_true',
                    help='Actually send. Without this flag the script is a DRY RUN.')
    ap.add_argument('--test-to',
                    help='Send a single preview to this address (uses the first eligible user for copy).')
    ap.add_argument('--cooldown-days', type=int, default=3,
                    help='Skip users contacted within this many days (default 3).')
    ap.add_argument('--limit', type=int, default=0,
                    help='Cap the number of sends this run (0 = no cap).')
    ap.add_argument('--sleep', type=float, default=0.4, help='Seconds between sends.')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        rows = candidates()
        watermark, recent = load_sent_state(args.cooldown_days)

        fresh = []
        skipped_seen = skipped_cooldown = 0
        for r in rows:
            latest = r['echo'].get('latest_at') or ''
            if latest and latest <= watermark.get(r['user_id'], ''):
                skipped_seen += 1
                continue
            if r['user_id'] in recent:
                skipped_cooldown += 1
                continue
            fresh.append(r)

        print(f"Echo window: last {CHALLENGE_ECHO_WINDOW_DAYS} days (shared with the in-app banner)")
        print(f"Eligible (challenge taken + opted-in verified): {len(rows)}")
        print(f"  skipped (already told about this acceptance): {skipped_seen}")
        print(f"  skipped (contacted within {args.cooldown_days}d): {skipped_cooldown}")
        print(f"  to contact this run: {len(fresh)}"
              + (f" (capped at {args.limit})" if args.limit and len(fresh) > args.limit else ""))
        print()
        print(f"  {'user':6} {'taken':>5} {'beaten_by':>12} {'best_dE':>7}  locale  email")
        for r in fresh:
            beat = r['echo'].get('best_beat')
            who = (('guest' if beat['is_guest'] else str(beat['user'])) if beat else '—')
            de = f"{float(beat['delta_e']):.2f}" if beat else '—'
            print(f"  {r['user_id']:6} {r['echo']['count']:>5} {who:>12} {de:>7}  "
                  f"{(r.get('locale') or 'en'):6}  {r['email']}")

        if args.test_to:
            sample = fresh[0] if fresh else (rows[0] if rows else None)
            if sample is None:
                print("\nNo eligible user to build preview copy from.")
                return 1
            preview = dict(sample)
            preview['email'] = args.test_to
            # Never mail a real participant's unsubscribe token to the tester:
            # the link is live, and one click would opt THEM out, not you. The
            # sentinel still signs a valid token, but it resolves to no user, so
            # /email/unsubscribe answers 404 and nothing happens.
            preview['user_id'] = 'PREVEW'
            send_one(preview)
            print(f"\nTest echo sent to {args.test_to} (copy modeled on user {sample['user_id']}, "
                  "sent under a preview id so the unsubscribe link is inert).")
            return 0

        if not args.send:
            print("\nDRY RUN — nothing sent. Re-run with --send to deliver.")
            if fresh:
                ctx = build_context(fresh[0])
                print("\nPreview (first recipient):")
                print(f"  subject:  {ctx['subject']}")
                print(f"  headline: {ctx['headline']}")
                print(f"  subhead:  {ctx['subhead']}")
                print(f"  subhead2: {ctx['subhead2']}")
                print(f"  cta:      {ctx['cta_label']} -> {ctx['cta_url']}")
            return 0

        batch = fresh[:args.limit] if args.limit else fresh
        sent = failed = 0
        for r in batch:
            try:
                send_one(r)
                append_sent_log(r['user_id'], datetime.utcnow().isoformat(),
                                r['echo'].get('latest_at'))
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
