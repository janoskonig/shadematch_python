#!/usr/bin/env python3
"""
Create database shadematch_v2 on the same PostgreSQL server as DATABASE_URL,
clone the schema from the source DB, then copy:

  1) All users with created_at on/after the registration cutoff (default: today 14:30
     in --tz, i.e. 2:30 pm).
  2) All rows tied to those users (attempts, sessions, events, progress, awards, etc.),
     regardless of when that activity occurred.

Default cutoff time is 14:30; use --hour and --minute to override.

Usage:
  python scripts/migrate_to_shadematch_v2.py
  python scripts/migrate_to_shadematch_v2.py --date 2026-04-24 --hour 14 --minute 30 --tz Europe/Budapest
  python scripts/migrate_to_shadematch_v2.py --dry-run

Loads DATABASE_URL from repo-root .env (python-dotenv) or the environment.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, time
from urllib.parse import urlparse, urlunparse

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

from sqlalchemy import MetaData, create_engine


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load_env() -> None:
    env_path = os.path.join(REPO_ROOT, ".env")
    if load_dotenv and os.path.isfile(env_path):
        load_dotenv(env_path)


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def admin_database_url(source_url: str, admin_db: str = "postgres") -> str:
    p = urlparse(normalize_database_url(source_url))
    path = "/" + admin_db
    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, p.fragment))


def target_database_url(source_url: str, new_db: str) -> str:
    p = urlparse(normalize_database_url(source_url))
    return urlunparse((p.scheme, p.netloc, "/" + new_db, p.params, p.query, p.fragment))


def mask_netloc(netloc: str) -> str:
    """Hide password in host.netloc for safe logging."""
    if "@" not in netloc:
        return netloc
    creds, host = netloc.rsplit("@", 1)
    if ":" in creds:
        user, _pw = creds.split(":", 1)
        return f"{user}:***@{host}"
    return f"***@{host}"


def database_name_from_url(url: str) -> str:
    p = urlparse(normalize_database_url(url))
    name = (p.path or "/").lstrip("/")
    if not name:
        raise ValueError("DATABASE_URL has no database name in path")
    return name


def parse_cutoff_naive_utc(args: argparse.Namespace) -> datetime:
    """Return naive UTC datetime for DB comparisons (app stores UTC without tz)."""
    d = date.fromisoformat(args.date)
    t = time(hour=args.hour, minute=args.minute, second=0)
    if args.tz.upper() == "UTC" or args.tz == "UTC":
        return datetime.combine(d, t)
    if ZoneInfo is None:
        raise SystemExit("Python 3.9+ zoneinfo required for --tz other than UTC")
    local = datetime.combine(d, t, tzinfo=ZoneInfo(args.tz))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def run_pg_dump_schema(source_url: str) -> bytes:
    proc = subprocess.run(
        ["pg_dump", "--schema-only", "--no-owner", "--no-acl", source_url],
        capture_output=True,
        timeout=600,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", errors="replace"))
        raise RuntimeError("pg_dump --schema-only failed")
    return proc.stdout


def dest_has_table(url: str, table: str = "users") -> bool:
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = %s
                )
                """,
                (table,),
            )
            return bool(cur.fetchone()[0])
    finally:
        conn.close()


def clone_schema_sqlalchemy(source_url: str, dest_url: str) -> None:
    """Recreate public tables on dest by reflecting the source (no pg_dump required)."""
    src = create_engine(source_url, future=True)
    dst = create_engine(dest_url, future=True)
    meta = MetaData()
    meta.reflect(bind=src)
    meta.create_all(bind=dst)


def apply_sql_bytes(dest_url: str, sql_bytes: bytes) -> None:
    proc = subprocess.run(
        ["psql", "-v", "ON_ERROR_STOP=1", "-q", dest_url],
        input=sql_bytes,
        capture_output=True,
        timeout=600,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", errors="replace"))
        raise RuntimeError("psql schema apply failed")


def ensure_database(admin_url: str, db_name: str, dry_run: bool) -> bool:
    """
    Create the database if missing.
    Returns True if the database already existed (caller should skip schema-only pg_dump).
    """
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (db_name,),
            )
            exists = cur.fetchone() is not None
            if exists:
                print(f"Database {db_name!r} already exists; skipping CREATE DATABASE.")
                return True
            if dry_run:
                print(f"[dry-run] Would CREATE DATABASE {db_name!r}")
                return False
            cur.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
            )
            print(f"Created database {db_name!r}.")
            return False
    finally:
        conn.close()


def fetchall_dict(cur, query, params=None):
    cur.execute(query, params or ())
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def public_table_names(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
        return {r[0] for r in cur.fetchall()}


def truncate_all_public_tables(conn) -> None:
    """Empty all public tables on the target (migration script is destructive on dest only)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
        tabs = [r[0] for r in cur.fetchall()]
        if not tabs:
            return
        cur.execute(
            sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(sql.Identifier(t) for t in tabs)
            )
        )


def insert_rows(dest, table: str, columns: list[str], rows: list[dict]) -> int:
    if not rows:
        return 0
    cols_sql = sql.SQL(", ").join(map(sql.Identifier, columns))
    insert_stmt = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(
        sql.Identifier(table), cols_sql
    )
    values = [[row[c] for c in columns] for row in rows]
    with dest.cursor() as cur:
        execute_values(cur, insert_stmt.as_string(dest), values, page_size=500)
    return len(rows)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--new-db", default="shadematch_v2", help="New database name (default: shadematch_v2)")
    parser.add_argument("--date", help="Calendar date YYYY-MM-DD (default: today in local system date)")
    parser.add_argument(
        "--hour",
        type=int,
        default=14,
        help="Local hour for registration cutoff (default 14 = 2pm for 2:30 use --minute 30).",
    )
    parser.add_argument(
        "--minute",
        type=int,
        default=30,
        help="Local minute for registration cutoff (default 30 → 2:30 pm with default hour).",
    )
    parser.add_argument(
        "--tz",
        default="UTC",
        help="Timezone for the cutoff clock time (default UTC). Use e.g. Europe/Budapest for local time.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan only; no DDL/DML")
    parser.add_argument("--skip-schema", action="store_true", help="Assume target DB already has identical schema")
    parser.add_argument(
        "--force-schema",
        action="store_true",
        help="Apply pg_dump schema even if the target database already existed (may fail if objects exist).",
    )
    args = parser.parse_args()

    raw = os.getenv("DATABASE_URL")
    if not raw:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    source_url = normalize_database_url(raw.strip())
    source_db = database_name_from_url(source_url)
    if source_db == args.new_db:
        print("Source and target database names are the same; aborting.", file=sys.stderr)
        return 1

    if not args.date:
        args.date = date.today().isoformat()

    cutoff = parse_cutoff_naive_utc(args)
    print(
        "Registration cutoff (users.created_at >= this instant, naive UTC in DB): "
        f"{cutoff.isoformat(sep=' ')}"
    )

    admin_url = admin_database_url(source_url)
    dest_url = target_database_url(source_url, args.new_db)

    if args.dry_run:
        print(f"[dry-run] Source DB: {source_db!r}, target: {args.new_db!r}")
        print(
            "[dry-run] Would copy users with created_at >= cutoff, then all rows "
            "for those user_ids (attempts, sessions, events, progress, etc.)."
        )
        return 0

    target_pre_existed = ensure_database(admin_url, args.new_db, dry_run=False)
    has_schema = dest_has_table(dest_url, "users")

    if not args.skip_schema:
        if has_schema and not args.force_schema:
            print(
                "Target already has a public schema (found table 'users'); skipping DDL clone. "
                "Use --force-schema to attempt re-apply, or DROP DATABASE and re-run."
            )
        else:
            if shutil.which("pg_dump") and shutil.which("psql"):
                print("Dumping schema from source (pg_dump)...")
                schema_sql = run_pg_dump_schema(source_url)
                print(f"Applying schema to {args.new_db!r} ({len(schema_sql)} bytes)...")
                apply_sql_bytes(dest_url, schema_sql)
            else:
                print(
                    "pg_dump/psql not on PATH; cloning schema via SQLAlchemy reflect+create_all "
                    "(install PostgreSQL client tools for an exact pg_dump match)."
                )
                clone_schema_sqlalchemy(source_url, dest_url)

    src = psycopg2.connect(source_url)
    dst = psycopg2.connect(dest_url)
    try:
        with src.cursor() as s:
            src_tables = public_table_names(src)

        print("Truncating all public tables on target before copy...")
        truncate_all_public_tables(dst)

        # 1) Full reference copy of target_colors (small, satisfies FKs)
        with src.cursor() as s:
            rows = fetchall_dict(s, "SELECT * FROM target_colors ORDER BY id")
        if rows:
            cols = list(rows[0].keys())
            n = insert_rows(dst, "target_colors", cols, rows)
            print(f"target_colors: inserted {n} rows (full table).")

        # Users registered on/after cutoff; all other copied rows are scoped to these ids.
        user_ids: list[str] = []
        if "users" in src_tables:
            with src.cursor() as s:
                s.execute(
                    "SELECT id::text FROM users WHERE created_at >= %s ORDER BY id",
                    (cutoff,),
                )
                user_ids = [r[0] for r in s.fetchall() if r[0]]

        if not user_ids:
            print("No users with created_at on or after cutoff; skipping user-scoped tables.")
        else:
            print(f"Importing {len(user_ids)} user(s) registered on/after cutoff and all related rows.")

            with src.cursor() as s:
                s.execute("SELECT * FROM users WHERE id = ANY(%s)", (user_ids,))
                cols = [c[0] for c in s.description]
                urows = [dict(zip(cols, row)) for row in s.fetchall()]
            n = insert_rows(dst, "users", cols, urows)
            print(f"users: inserted {n} rows.")

            uid_params = (user_ids,)

            if "mixing_attempts" in src_tables:
                with src.cursor() as s:
                    rows = fetchall_dict(
                        s,
                        "SELECT * FROM mixing_attempts WHERE user_id IS NOT NULL AND user_id = ANY(%s)",
                        uid_params,
                    )
                if rows:
                    cols = list(rows[0].keys())
                    n = insert_rows(dst, "mixing_attempts", cols, rows)
                    print(f"mixing_attempts: inserted {n} rows.")

            if "mixing_attempt_events" in src_tables and "mixing_attempts" in src_tables:
                with src.cursor() as s:
                    s.execute(
                        "SELECT e.* FROM mixing_attempt_events e "
                        "JOIN mixing_attempts a ON a.attempt_uuid = e.attempt_uuid "
                        "WHERE a.user_id IS NOT NULL AND a.user_id = ANY(%s)",
                        uid_params,
                    )
                    cols = [c[0] for c in s.description]
                    erows = [dict(zip(cols, row)) for row in s.fetchall()]
                if erows:
                    n = insert_rows(dst, "mixing_attempt_events", cols, erows)
                    print(f"mixing_attempt_events: inserted {n} rows.")

            if "mixing_sessions" in src_tables:
                with src.cursor() as s:
                    rows = fetchall_dict(
                        s,
                        'SELECT * FROM mixing_sessions WHERE user_id IS NOT NULL AND user_id = ANY(%s)',
                        uid_params,
                    )
                if rows:
                    cols = list(rows[0].keys())
                    n = insert_rows(dst, "mixing_sessions", cols, rows)
                    print(f"mixing_sessions: inserted {n} rows.")

            tab_by_user = [
                "user_progress",
                "user_target_color_stats",
                "user_awards",
                "daily_challenge_runs",
                "daily_challenge_winners",
                "push_subscriptions",
                "email_verification_tokens",
                "analytics_events",
            ]
            for table in tab_by_user:
                if table not in src_tables:
                    continue
                with src.cursor() as s:
                    rows = fetchall_dict(
                        s,
                        f"SELECT * FROM {table} WHERE user_id IS NOT NULL AND user_id = ANY(%s)",
                        uid_params,
                    )
                if rows:
                    cols = list(rows[0].keys())
                    n = insert_rows(dst, table, cols, rows)
                    print(f"{table}: inserted {n} rows.")

        # Legacy session table if present
        with src.cursor() as s:
            s.execute(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = 'session'
                )
                """
            )
            has_session = s.fetchone()[0]
        if has_session and user_ids:
            with src.cursor() as s:
                s.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'session'
                    """
                )
                scols = {r[0] for r in s.fetchall()}
            if "user_id" in scols:
                with src.cursor() as s:
                    rows = fetchall_dict(
                        s,
                        "SELECT * FROM session WHERE user_id IS NOT NULL AND user_id = ANY(%s)",
                        (user_ids,),
                    )
                if rows:
                    cols = list(rows[0].keys())
                    n = insert_rows(dst, "session", cols, rows)
                    print(f"session: inserted {n} rows.")
            else:
                print("session: table exists but has no user_id column; skipped for user-scoped import.")

        # Align serial sequences with inserted max ids (whitelisted table/column names only).
        seq_tables = [
            ("mixing_sessions", "id"),
            ("mixing_attempt_events", "id"),
            ("user_progress", "id"),
            ("user_target_color_stats", "id"),
            ("user_awards", "id"),
            ("daily_challenge_runs", "id"),
            ("daily_challenge_winners", "id"),
            ("push_subscriptions", "id"),
            ("email_verification_tokens", "id"),
            ("analytics_events", "id"),
        ]
        with dst.cursor() as d:
            for tbl, col in seq_tables:
                if tbl not in src_tables:
                    continue
                d.execute(
                    """
                    SELECT EXISTS (
                      SELECT 1 FROM information_schema.tables
                      WHERE table_schema = 'public' AND table_name = %s
                    )
                    """,
                    (tbl,),
                )
                if not d.fetchone()[0]:
                    continue
                d.execute("SELECT pg_get_serial_sequence(%s, %s)", (tbl, col))
                row = d.fetchone()
                if not row or not row[0]:
                    continue
                seq_name = row[0]
                d.execute(
                    sql.SQL(
                        "SELECT setval({}::regclass, "
                        "COALESCE((SELECT MAX({}) FROM {}), 1), true)"
                    ).format(
                        sql.Literal(seq_name),
                        sql.Identifier(col),
                        sql.Identifier(tbl),
                    )
                )
        dst.commit()
        p = urlparse(dest_url)
        print("Done. Point DATABASE_URL at the same server with database name:")
        print(f"  postgresql://{mask_netloc(p.netloc)}/{args.new_db}")
    finally:
        src.close()
        dst.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
