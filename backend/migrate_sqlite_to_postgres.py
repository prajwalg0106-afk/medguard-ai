"""Copy an existing SQLite MedGuard database into a new PostgreSQL database.

Run with MEDGUARD_DATABASE_URL set. The target must be empty. IDs and row values
from SQLite win on conflicts, allowing this to overwrite only rows that the
application's initial empty database setup would otherwise have seeded.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from backend import db


TABLE_ORDER = (
    "hospitals", "medicines", "suppliers", "inventory", "sales_transactions",
    "supplier_medicines", "disruptions", "recommendations", "approvals", "shipments",
    "audit_events", "settings", "users", "pharmacy_memberships", "user_sessions",
    "medicine_batches", "stock_movements", "notifications", "purchase_orders",
    "purchase_order_items", "shortage_alerts", "email_delivery_logs",
    "hospital_transfer_requests", "transfer_history",
)


def migrate(source_path: Path) -> dict[str, int]:
    if not db.is_postgres():
        raise RuntimeError("Set MEDGUARD_DATABASE_URL to a PostgreSQL connection URL before running this migration.")
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source_path}")

    import psycopg

    with psycopg.connect(db.DATABASE_URL, connect_timeout=10) as target:
        populated = []
        for table in TABLE_ORDER:
            exists = target.execute(f"SELECT to_regclass('{table}')").fetchone()[0]
            if exists and target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                populated.append(table)
        if populated:
            raise RuntimeError("The PostgreSQL target is not empty (records exist in: " + ", ".join(populated) + "). Choose a new database to avoid overwriting data.")

    db.initialize(seed_if_empty=False)
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        available = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        copied: dict[str, int] = {}
        with db.connect() as target:
            for table in TABLE_ORDER:
                if table not in available:
                    continue
                source_columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
                target_columns = {row[0] for row in target.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?",
                    (table,),
                ).fetchall()}
                columns = [column for column in source_columns if column in target_columns]
                if not columns:
                    continue
                rows = source.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
                if not rows:
                    copied[table] = 0
                    continue
                primary_keys = [row[1] for row in sorted(
                    (item for item in source.execute(f"PRAGMA table_info({table})") if item[5]), key=lambda item: item[5]
                ) if row[1] in columns]
                placeholders = ",".join("%s" for _ in columns)
                names = ",".join(columns)
                if primary_keys:
                    update_columns = [column for column in columns if column not in primary_keys]
                    conflict = " ON CONFLICT (" + ",".join(primary_keys) + ") "
                    if update_columns:
                        conflict += "DO UPDATE SET " + ",".join(f"{column}=EXCLUDED.{column}" for column in update_columns)
                    else:
                        conflict += "DO NOTHING"
                else:
                    conflict = ""
                target.executemany(
                    f"INSERT INTO {table} ({names}) VALUES ({placeholders}){conflict}",
                    [tuple(row[column] for column in columns) for row in rows],
                )
                copied[table] = len(rows)
        return copied
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("backend/medguard.sqlite3"), help="Path to the SQLite database to copy")
    args = parser.parse_args()
    counts = migrate(args.source)
    print("SQLite records copied to PostgreSQL:")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    print("Keep the SQLite backup until you have checked the PostgreSQL app and record counts.")


if __name__ == "__main__":
    main()
