#!/usr/bin/env python3
"""Add Hungarian color glosses to target_colors.name_hu.

Reads translations/color_names_hu.csv (header: name_en,name_hu) and sets
target_colors.name_hu = <gloss> for every row whose English name matches
name_en exactly. The glosses are short Hungarian descriptions of the color
itself (e.g. "Merlot" -> "bordó"), shown by the UI as 'English — magyar'
when locale=hu.

Usage:
    PYTHONPATH=. python3 scripts/add_hu_color_glosses.py --env shadestudy.env [--commit]

Without --commit it runs a dry-run (prints the planned updates, no write).
Idempotent: rows whose name_hu already equals the gloss are left untouched,
so re-running with --commit is a no-op.
"""
import argparse
import csv
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GLOSS_CSV = REPO / 'translations' / 'color_names_hu.csv'


def load_env(env_path):
    for line in Path(env_path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())


def load_glosses():
    """Read the translations CSV into {name_en: name_hu}."""
    glosses = {}
    with open(GLOSS_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ['name_en', 'name_hu']:
            raise SystemExit(
                f'unexpected header in {GLOSS_CSV}: {reader.fieldnames} '
                f"(expected ['name_en', 'name_hu'])")
        for lineno, row in enumerate(reader, start=2):
            name_en = (row['name_en'] or '').strip()
            name_hu = (row['name_hu'] or '').strip()
            if not name_en or not name_hu:
                raise SystemExit(f'{GLOSS_CSV}:{lineno}: empty name_en or name_hu')
            if name_en in glosses:
                raise SystemExit(f'{GLOSS_CSV}:{lineno}: duplicate name_en {name_en!r}')
            glosses[name_en] = name_hu
    return glosses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--env', required=True, help='env file with DATABASE_URL')
    ap.add_argument('--commit', action='store_true', help='actually write (else dry-run)')
    args = ap.parse_args()

    load_env(REPO / args.env)
    if not os.environ.get('DATABASE_URL'):
        raise SystemExit('DATABASE_URL not set from env file')

    glosses = load_glosses()
    print(f'glosses in CSV: {len(glosses)}')

    from app import create_app, db
    from app.models import TargetColor

    app = create_app()
    with app.app_context():
        targets = TargetColor.query.order_by(TargetColor.id).all()
        if not targets:
            raise SystemExit('no target_colors rows found')
        print(f'target_colors rows: {len(targets)}')

        matched = updated = unchanged = 0
        matched_en = set()
        db_names_without_csv = []

        for tc in targets:
            gloss = glosses.get(tc.name)
            if gloss is None:
                db_names_without_csv.append(tc.name)
                continue
            matched += 1
            matched_en.add(tc.name)
            if tc.name_hu == gloss:
                unchanged += 1
                continue
            updated += 1
            prefix = 'UPDATE' if args.commit else 'DRY-RUN'
            print(f'{prefix}: id={tc.id} {tc.name!r}: name_hu '
                  f'{tc.name_hu!r} -> {gloss!r}')
            if args.commit:
                tc.name_hu = gloss

        if args.commit:
            db.session.commit()

        csv_without_db = sorted(set(glosses) - matched_en)

        print(f'\nmatched rows:   {matched}')
        print(f'updated rows:   {updated}' + ('' if args.commit else ' (dry-run, not written)'))
        print(f'already set:    {unchanged}')
        print(f'DB names with no CSV row: {len(set(db_names_without_csv))}')
        for name in sorted(set(db_names_without_csv)):
            print(f'  - {name}')
        print(f'CSV rows with no DB match: {len(csv_without_db)}')
        for name in csv_without_db:
            print(f'  - {name}')

        if not args.commit:
            print('\nDRY-RUN complete. Re-run with --commit to write.')


if __name__ == '__main__':
    main()
