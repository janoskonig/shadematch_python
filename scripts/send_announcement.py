#!/usr/bin/env python3
"""
One-off broadcast of a product-update announcement email to opted-in, verified users.

Eligibility mirrors the daily reminder path: email present, email_opt_in_reminders
true, email_verified_at set. Sends with one-click unsubscribe (RFC 8058) and
transactional=False (bulk headers), reusing app.email_utils.

Usage:
  python scripts/send_announcement.py --dry-run           # list recipients, send nothing
  python scripts/send_announcement.py --test-to a@b.com   # send ONE preview to that address
  python scripts/send_announcement.py                     # send to ALL eligible recipients

Loads DATABASE_URL / SMTP config from shadestudy.env via app.create_app.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app import create_app  # noqa: E402
from app.models import User  # noqa: E402
from app import email_utils  # noqa: E402

SUBJECT = "New colours, a fairer game · Új színek, igazságosabb verseny \U0001F3A8"


def eligible_users():
    return (
        User.query
        .filter(
            User.email.isnot(None),
            User.email_opt_in_reminders.is_(True),
            User.email_verified_at.isnot(None),
        )
        .order_by(User.created_at.asc())
        .all()
    )


def render_for(user_id: str):
    cta_url = email_utils.base_url()
    unsub = email_utils.build_unsubscribe_url(user_id)
    html, text = email_utils.render_email(
        'announcement',
        username=user_id,
        cta_url=cta_url,
        list_unsubscribe_url=unsub,
    )
    return html, text, unsub


def _send(to_email: str, user_id: str):
    html, text, unsub = render_for(user_id)
    email_utils.send_email(
        to_email=to_email,
        subject=SUBJECT,
        html=html,
        text=text,
        list_unsubscribe_url=unsub,
        one_click_unsubscribe=True,
        transactional=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true', help='List recipients, send nothing.')
    ap.add_argument('--test-to', help='Send a single preview to this address.')
    ap.add_argument('--sleep', type=float, default=0.4, help='Seconds between sends.')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        users = eligible_users()
        print(f"Eligible recipients (email + verified + opted-in): {len(users)}")
        for u in users:
            print(f"  {u.id}  {u.email}")

        if args.dry_run:
            print("\nDRY RUN — nothing sent.")
            return 0

        if args.test_to:
            u = User.query.filter(User.email == args.test_to).first()
            uid = u.id if u else 'PREVIEW'
            _send(args.test_to, uid)
            print(f"\nTest email sent to {args.test_to} (unsubscribe token for user={uid}).")
            return 0

        sent = failed = 0
        for u in users:
            try:
                _send(u.email, u.id)
                sent += 1
                print(f"  sent   -> {u.id} {u.email}")
            except Exception as exc:  # noqa: BLE001 — report and continue
                failed += 1
                print(f"  FAILED -> {u.id} {u.email}: {exc}")
            time.sleep(args.sleep)
        print(f"\nDone. sent={sent} failed={failed}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
