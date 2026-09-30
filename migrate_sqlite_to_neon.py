"""Migrate existing SQLite expense tracker data to Neon PostgreSQL.

Usage:
    python migrate_sqlite_to_neon.py [--sqlite PATH] [--database-url URL] [--dry-run]
"""
import argparse
import os
import sqlite3
import sys
from urllib.parse import urlparse
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

from db import SCHEMA

load_dotenv()


def sanitize_url(url: str) -> str:
    """Mask password in connection URL for safe logging."""
    try:
        p = urlparse(url)
        if p.password:
            netloc = f"{p.username}:***@{p.hostname}"
            if p.port:
                netloc += f":{p.port}"
            return p._replace(netloc=netloc).geturl()
    except Exception:
        pass
    return "<DATABASE_URL>"


def migrate(sqlite_path: str, pg_url: str, dry_run: bool = False) -> dict:
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"SQLite database file not found at: {sqlite_path}")

    print(f"Connecting to SQLite: {sqlite_path}")
    sq_conn = sqlite3.connect(sqlite_path)
    sq_conn.row_factory = sqlite3.Row

    print(f"Connecting to PostgreSQL: {sanitize_url(pg_url)}")
    pg_conn = psycopg.connect(pg_url, row_factory=dict_row)

    try:
        # Ensure target schema exists in PostgreSQL
        with pg_conn.cursor() as cur:
            cur.execute(SCHEMA)
        pg_conn.commit()

        # 1. Migrate month_budgets
        sq_budgets = sq_conn.execute("SELECT month, category, amount FROM month_budgets").fetchall()
        print(f"Found {len(sq_budgets)} budget rows in SQLite.")

        budget_migrated = 0
        with pg_conn.cursor() as cur:
            for b in sq_budgets:
                cur.execute(
                    """
                    INSERT INTO month_budgets (month, category, amount)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (month, category) DO UPDATE
                    SET amount = EXCLUDED.amount
                    """,
                    (b["month"], b["category"], b["amount"]),
                )
                budget_migrated += 1

        # 2. Migrate expenses (idempotent / deduplicated)
        sq_expenses = sq_conn.execute(
            "SELECT id, date, description, category, amount, notes FROM expenses ORDER BY id"
        ).fetchall()
        print(f"Found {len(sq_expenses)} expense rows in SQLite.")

        with pg_conn.cursor() as cur:
            cur.execute("SELECT id, TO_CHAR(date, 'YYYY-MM-DD') AS date, description, category, amount, notes FROM expenses")
            pg_existing = cur.fetchall()

        existing_ids = {r["id"] for r in pg_existing}
        # Deduplication key based on transaction details
        existing_signatures = {
            (r["date"], r["description"].strip().lower(), r["category"], r["amount"])
            for r in pg_existing
        }

        inserted_expenses = 0
        skipped_expenses = 0

        with pg_conn.cursor() as cur:
            for exp in sq_expenses:
                exp_date = str(exp["date"])[:10]
                exp_desc = str(exp["description"]).strip().lower()
                exp_cat = exp["category"]
                exp_amt = exp["amount"]
                exp_notes = str(exp["notes"] or "")

                sig = (exp_date, exp_desc, exp_cat, exp_amt)
                if sig in existing_signatures:
                    skipped_expenses += 1
                    continue

                if exp["id"] not in existing_ids:
                    # Insert preserving the original ID
                    cur.execute(
                        """
                        INSERT INTO expenses (id, date, description, category, amount, notes)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (exp["id"], exp_date, exp["description"], exp["category"], exp_amt, exp_notes),
                    )
                    existing_ids.add(exp["id"])
                else:
                    # ID conflict with a different record: insert with auto-generated ID
                    cur.execute(
                        """
                        INSERT INTO expenses (date, description, category, amount, notes)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (exp_date, exp["description"], exp["category"], exp_amt, exp_notes),
                    )

                existing_signatures.add(sig)
                inserted_expenses += 1

            # Update PostgreSQL sequence to prevent future primary key collisions
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('expenses', 'id'), COALESCE((SELECT MAX(id) FROM expenses), 1))"
            )

        if dry_run:
            pg_conn.rollback()
            print("\n[DRY RUN] Changes rolled back. No database modifications committed.")
        else:
            pg_conn.commit()
            print("\n[SUCCESS] Migration committed successfully.")

        result = {
            "budgets_processed": budget_migrated,
            "expenses_inserted": inserted_expenses,
            "expenses_skipped": skipped_expenses,
            "total_sqlite_expenses": len(sq_expenses),
        }
        print(f"Summary: {budget_migrated} budgets processed, {inserted_expenses} expenses inserted, {skipped_expenses} duplicates skipped.")
        return result

    finally:
        sq_conn.close()
        pg_conn.close()


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite data to Neon PostgreSQL")
    parser.add_argument(
        "--sqlite",
        default=os.environ.get("DATABASE_PATH") or os.path.join(os.path.dirname(__file__), "data", "expenses.db"),
        help="Path to SQLite database file (default: data/expenses.db or DATABASE_PATH)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Neon PostgreSQL connection URL (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the migration without committing changes",
    )
    args = parser.parse_args()

    if not args.database_url:
        print("ERROR: DATABASE_URL is not set. Please set DATABASE_URL or pass --database-url.")
        sys.exit(1)

    try:
        migrate(args.sqlite, args.database_url, dry_run=args.dry_run)
    except Exception as e:
        print(f"ERROR: Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
