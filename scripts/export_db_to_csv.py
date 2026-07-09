#!/usr/bin/env python3
"""
Export every public table of a Postgres DB to CSV (read-only).

Reads DATABASE_URL from shadestudy.env, optionally swaps the database name,
and dumps each table to data/<dbname>/<table>.csv. Also prints row counts.

Usage (run on a machine that can reach the DB host):
  # export the current (v2) DB (DATABASE_URL):
  python scripts/export_db_to_csv.py

  # export the old v1 DB (OLD_DATABASE_URL = mixing_sessions):
  python scripts/export_db_to_csv.py --var OLD_DATABASE_URL

Requires: sqlalchemy + a postgres driver (already used by the app), pandas.
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pandas as pd
from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[1]


def load_url(var: str) -> str:
    env = (REPO / "shadestudy.env").read_text()
    m = re.search(rf"^{re.escape(var)}=(\S+)", env, re.MULTILINE)
    if not m:
        raise SystemExit(f"{var} not found in shadestudy.env")
    return m.group(1).strip().strip('"').strip("'")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--var", default="DATABASE_URL",
                    help="Env var in shadestudy.env to read the URL from "
                         "(use OLD_DATABASE_URL for the v1 mixing_sessions DB)")
    ap.add_argument("--database", help="Optional: override the DB name in the URL")
    args = ap.parse_args()

    url = load_url(args.var)
    # SQLAlchemy wants the postgresql+driver scheme; normalise common prefixes.
    url = url.replace("postgres://", "postgresql://", 1)

    if args.database:
        p = urlparse(url)
        url = urlunparse(p._replace(path="/" + args.database))

    dbname = urlparse(url).path.lstrip("/") or "db"
    out = REPO / "data" / dbname
    out.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, connect_args={"connect_timeout": 15})
    with engine.connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "select tablename from pg_tables where schemaname='public' order by 1"))]
        print(f"DB '{dbname}': {len(tables)} tables -> {out}")
        for t in tables:
            n = conn.execute(text(f'select count(*) from "{t}"')).scalar()
            df = pd.read_sql(text(f'select * from "{t}"'), conn)
            df.to_csv(out / f"{t}.csv", index=False)
            print(f"  {t:32s} {n:>8} rows")
    print("Done. CSVs are in:", out)


if __name__ == "__main__":
    main()
