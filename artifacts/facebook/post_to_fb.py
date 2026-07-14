#!/usr/bin/env python3
"""
Post ShadeMatch "Color Challenge" cards to a Facebook Page via the Meta Graph API.

Safe by default: nothing is published unless you pass --live. Without it the
script only prints the exact plan (dry run). Reuses challenge.py to build the
card + bilingual caption, so one command generates AND posts/schedules.

Credentials live in shadestudy.env (gitignored), NEVER in this file:
    FB_PAGE_ID=1234567890
    FB_PAGE_ACCESS_TOKEN=EAAG...        # long-lived Page token, pages_manage_posts

Examples:
    # dry run — see exactly what would be posted (no token needed):
    python post_to_fb.py --num 3 --pick 11

    # publish now (requires env creds):
    python post_to_fb.py --num 3 --pick 11 --live

    # schedule for a future local time (10 min .. 6 months ahead):
    python post_to_fb.py --num 3 --pick 11 --schedule "2026-07-15 09:00" --live

    # run a whole queue file (see challenge_queue.example.json):
    python post_to_fb.py --queue challenge_queue.json --live
"""
from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path

import challenge  # local module (same folder)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ENV = REPO / 'shadestudy.env'
GRAPH_VERSION = 'v21.0'          # bump when Meta deprecates; any recent version works

# ---------------------------------------------------------------- creds ------
def env_value(key):
    if not ENV.exists():
        return None
    m = re.search(rf'^{re.escape(key)}=(\S+)', ENV.read_text(), re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else None

def require_creds():
    pid, tok = env_value('FB_PAGE_ID'), env_value('FB_PAGE_ACCESS_TOKEN')
    if not pid or not tok:
        sys.exit("✗ Missing FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN in shadestudy.env.\n"
                 "  Add them (see the setup steps), then re-run with --live.")
    return pid, tok

# ---------------------------------------------------------------- schedule ---
def to_unix(when: str) -> int:
    """Parse 'YYYY-MM-DD HH:MM' (local time) -> unix seconds; validate window."""
    try:
        ts = int(time.mktime(time.strptime(when, '%Y-%m-%d %H:%M')))
    except ValueError:
        sys.exit(f"✗ Bad --schedule time {when!r}; use 'YYYY-MM-DD HH:MM'.")
    now = int(time.time())
    if not (now + 600 <= ts <= now + 6 * 30 * 24 * 3600):
        sys.exit("✗ Scheduled time must be 10 minutes to ~6 months from now (Meta rule).")
    return ts

# ---------------------------------------------------------------- build ------
def build(entry) -> tuple[Path, str, str]:
    """Return (square_png, caption, answer_line) for a queue/CLI entry."""
    rows = challenge.load_targets()
    if entry.get('target') and entry.get('pigments'):
        used, target_hex, answer = entry['pigments'], entry['target'].upper(), '(manual)'
    else:
        if entry.get('pick') is not None:
            row = next((r for r in rows if r['idx'] == entry['pick']), None)
        else:
            row = next((r for r in rows if r['name'].lower() == entry.get('name', '').lower()), None)
        if row is None:
            sys.exit(f"✗ No target for entry {entry!r}")
        used, target_hex = row['used'], row['hex']
        answer = f"{row['name'] or 'target'} · {target_hex} · {challenge.recipe_str(row['counts'], 'en')}"
    challenge.render(entry['num'], target_hex, used)   # writes both formats
    square = HERE / f"shadestudy_fb_challenge{entry['num']}_square.png"
    return square, challenge.caption(entry['num'], used, None), answer

# ---------------------------------------------------------------- post -------
def post(page_id, token, image: Path, caption: str, when_ts=None, live=False):
    endpoint = f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/photos"
    plan = (f"    → POST {endpoint}\n"
            f"      image:    {image.relative_to(REPO)}\n"
            f"      schedule: {'IMMEDIATE' if not when_ts else time.strftime('%Y-%m-%d %H:%M', time.localtime(when_ts))}\n"
            f"      caption:  {caption.splitlines()[0]} …")
    if not live:
        print("  [DRY RUN — nothing published]\n" + plan)
        return None
    import requests
    data = {'caption': caption, 'access_token': token}
    if when_ts:
        data['published'] = 'false'
        data['scheduled_publish_time'] = when_ts
    with open(image, 'rb') as fh:
        r = requests.post(endpoint, data=data, files={'source': fh}, timeout=60)
    if not r.ok:
        sys.exit(f"✗ Graph API error {r.status_code}: {r.text}")
    res = r.json()
    print(f"  ✓ {'scheduled' if when_ts else 'published'} — id {res.get('post_id') or res.get('id')}")
    return res

# ---------------------------------------------------------------- main -------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--num', type=int)
    ap.add_argument('--pick', type=int)
    ap.add_argument('--name')
    ap.add_argument('--target'); ap.add_argument('--pigments')
    ap.add_argument('--schedule', help="Local 'YYYY-MM-DD HH:MM' (10min..6mo ahead)")
    ap.add_argument('--queue', help='JSON file: list of {num, pick|name, schedule?}')
    ap.add_argument('--live', action='store_true', help='Actually publish (default: dry run)')
    args = ap.parse_args()

    page_id, token = (require_creds() if args.live else ('<PAGE_ID>', '<TOKEN>'))
    if not args.live:
        print("═══ DRY RUN — pass --live to publish for real ═══\n")

    # assemble entries
    if args.queue:
        entries = json.loads(Path(args.queue).read_text())
    elif args.num:
        e = {'num': args.num, 'pick': args.pick, 'name': args.name,
             'target': args.target, 'pigments': args.pigments.split(',') if args.pigments else None}
        if args.schedule: e['schedule'] = args.schedule
        entries = [e]
    else:
        ap.error('give --num (with --pick/--name) or --queue')

    for e in entries:
        print(f"■ Challenge #{e['num']}")
        image, caption, answer = build(e)
        when_ts = to_unix(e['schedule']) if e.get('schedule') else None
        post(page_id, token, image, caption, when_ts, args.live)
        print(f"    answer (reveal later): {answer}\n")

if __name__ == '__main__':
    main()
