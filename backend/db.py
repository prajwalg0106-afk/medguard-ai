from __future__ import annotations

import json
import sqlite3
import uuid
import hashlib
import secrets
import shutil
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("MEDGUARD_DB_PATH", str(BASE_DIR / "medguard.sqlite3")))
SEED_PATH = BASE_DIR / "data" / "demo_data.json"

DEFAULT_SETTINGS = {
    "critical_days": "3",
    "warning_days": "7",
    "llm_provider": "deterministic",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "llama3.2",
}

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS hospitals (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, state TEXT NOT NULL, type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS medicines (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, unit TEXT NOT NULL, criticality TEXT NOT NULL,
  primary_supplier_id TEXT, manufacture_date TEXT, expiry_date TEXT
);
CREATE TABLE IF NOT EXISTS inventory (
  hospital_id TEXT NOT NULL, medicine_id TEXT NOT NULL, current_stock REAL NOT NULL,
  daily_demand REAL, safety_stock REAL NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (hospital_id, medicine_id)
);
CREATE TABLE IF NOT EXISTS sales_transactions (
  id TEXT PRIMARY KEY, hospital_id TEXT NOT NULL, medicine_id TEXT NOT NULL,
  quantity REAL NOT NULL, sold_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, reliability REAL NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS supplier_medicines (
  supplier_id TEXT NOT NULL, medicine_id TEXT NOT NULL, unit_price REAL NOT NULL,
  available_qty REAL NOT NULL, lead_days REAL NOT NULL,
  PRIMARY KEY (supplier_id, medicine_id)
);
CREATE TABLE IF NOT EXISTS disruptions (
  id TEXT PRIMARY KEY, event_type TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL,
  summary TEXT NOT NULL, hospital_id TEXT NOT NULL, medicine_id TEXT NOT NULL,
  impact_days REAL, status TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
  id TEXT PRIMARY KEY, disruption_id TEXT NOT NULL, recommendation_type TEXT NOT NULL,
  hospital_id TEXT NOT NULL, donor_hospital_id TEXT, supplier_id TEXT, medicine_id TEXT NOT NULL,
  quantity REAL NOT NULL, reason TEXT NOT NULL, benefit TEXT NOT NULL, risk TEXT NOT NULL,
  status TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL, decision TEXT NOT NULL,
  reviewer TEXT, reason TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shipments (
  id TEXT PRIMARY KEY, recommendation_id TEXT, source_type TEXT NOT NULL, source_id TEXT,
  source_label TEXT NOT NULL, destination_id TEXT NOT NULL, medicine_id TEXT NOT NULL,
  quantity REAL NOT NULL, status TEXT NOT NULL, eta_hours REAL NOT NULL,
  expected_arrival TEXT, route_json TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY, event_type TEXT NOT NULL, actor TEXT NOT NULL,
  entity_type TEXT, entity_id TEXT, summary TEXT NOT NULL,
  details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pharmacy_memberships (
  user_id TEXT NOT NULL, hospital_id TEXT NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (user_id,hospital_id),
  FOREIGN KEY (user_id) REFERENCES users(id), FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
);
CREATE TABLE IF NOT EXISTS user_sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS medicine_batches (
  id TEXT PRIMARY KEY, hospital_id TEXT NOT NULL, medicine_id TEXT NOT NULL, batch_number TEXT NOT NULL,
  manufacture_date TEXT, expiry_date TEXT, quantity_available REAL NOT NULL, source_label TEXT NOT NULL,
  is_sample INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
  UNIQUE(hospital_id,medicine_id,batch_number)
);
CREATE TABLE IF NOT EXISTS stock_movements (
  id TEXT PRIMARY KEY, hospital_id TEXT NOT NULL, medicine_id TEXT NOT NULL, batch_id TEXT,
  movement_type TEXT NOT NULL, quantity REAL NOT NULL, note TEXT, actor TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(batch_id) REFERENCES medicine_batches(id)
);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY, hospital_id TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL,
  title TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unread',
  simulated INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, read_at TEXT
);
CREATE TABLE IF NOT EXISTS purchase_orders (
  id TEXT PRIMARY KEY, hospital_id TEXT NOT NULL, supplier_id TEXT NOT NULL, status TEXT NOT NULL,
  total_quantity REAL NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  is_simulated INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS purchase_order_items (
  id TEXT PRIMARY KEY, purchase_order_id TEXT NOT NULL, medicine_id TEXT NOT NULL, quantity REAL NOT NULL,
  unit_price REAL, FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:10].upper()


def password_hash(password: str) -> str:
    """PBKDF2 hash for demo-local accounts. Production should delegate to an IdP."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 210_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 210_000).hex()
        return secrets.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=8)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 8000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_audit(
    connection: sqlite3.Connection,
    event_type: str,
    actor: str,
    summary: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict | None = None,
) -> str:
    event_id = new_id("EVT")
    connection.execute(
        """INSERT INTO audit_events
           (id,event_type,actor,entity_type,entity_id,summary,details_json,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (event_id, event_type, actor, entity_type, entity_id, summary,
         json.dumps(details or {}, ensure_ascii=False), now_iso()),
    )
    return event_id


def _load_seed() -> dict:
    with SEED_PATH.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def _seed_dataset(connection: sqlite3.Connection) -> None:
    data = _load_seed()
    connection.executemany(
        "INSERT INTO hospitals(id,name,city,state,type) VALUES (?,?,?,?,?)",
        [(h["id"], h["name"], h["city"], h["state"], h["type"]) for h in data["hospitals"]],
    )
    connection.executemany(
        """INSERT INTO medicines(id,name,unit,criticality,primary_supplier_id,manufacture_date,expiry_date)
           VALUES (?,?,?,?,?,?,?)""",
        [(m["id"], m["name"], m["unit"], m["criticality"], m["primary_supplier_id"],
          (datetime.now(timezone.utc)-timedelta(days=120)).date().isoformat(),
          (datetime.now(timezone.utc)+timedelta(days=540+(index*90))).date().isoformat())
         for index,m in enumerate(data["medicines"])],
    )
    stamp = now_iso()
    connection.executemany(
        """INSERT INTO inventory(hospital_id,medicine_id,current_stock,daily_demand,safety_stock,updated_at)
           VALUES (?,?,?,?,?,?)""",
        [(row[0], row[1], row[2], row[3], row[4], stamp) for row in data["inventory"]],
    )
    connection.executemany(
        "INSERT INTO suppliers(id,name,city,reliability,status) VALUES (?,?,?,?,?)",
        [(s["id"], s["name"], s["city"], s["reliability"], s["status"]) for s in data["suppliers"]],
    )
    connection.executemany(
        """INSERT INTO supplier_medicines(supplier_id,medicine_id,unit_price,available_qty,lead_days)
           VALUES (?,?,?,?,?)""",
        data["supplier_medicines"],
    )
    for item in data["shipments"]:
        eta = float(item["eta_hours"])
        expected = (datetime.now(timezone.utc) + timedelta(hours=eta)).isoformat(timespec="seconds").replace("+00:00", "Z") if item["status"] != "Delivered" else now_iso()
        connection.execute(
            """INSERT INTO shipments
               (id,recommendation_id,source_type,source_id,source_label,destination_id,medicine_id,
                quantity,status,eta_hours,expected_arrival,route_json,created_at,delivered_at)
               VALUES (?,NULL,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item["id"], item["source_type"], item["source_id"], item["source_label"],
             item["destination_id"], item["medicine_id"], item["quantity"], item["status"],
             eta, expected, json.dumps(item["route"]), stamp, now_iso() if item["status"] == "Delivered" else None),
        )
    for key, value in DEFAULT_SETTINGS.items():
        connection.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, value))


def _seed_medguard_two(connection: sqlite3.Connection) -> None:
    """Add non-destructive local accounts, batches and alerts to an existing demo database."""
    users = [
        ("USR-ADMIN", "admin@medguard.demo", "Platform Administrator", "Platform Administrator", "MedGuardDemo!2026"),
        ("USR-ASTER", "owner@aster.demo", "Aster Pharmacy Owner", "Pharmacy Owner", "AsterDemo!2026"),
        ("USR-LAKE", "manager@lakeside.demo", "Lakeside Inventory Manager", "Pharmacist / Inventory Manager", "LakesideDemo!2026"),
        ("USR-READ", "viewer@aster.demo", "Aster Read-only User", "Read-only User", "ViewerDemo!2026"),
    ]
    for user_id, email, name, role, password in users:
        if not connection.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            connection.execute("INSERT INTO users(id,email,display_name,password_hash,role,active,created_at) VALUES (?,?,?,?,?,?,?)",
                (user_id, email, name, password_hash(password), role, 1, now_iso()))
    memberships = [("USR-ADMIN", "ASTR", "Platform Administrator"), ("USR-ASTER", "ASTR", "Pharmacy Owner"),
                   ("USR-LAKE", "LAKE", "Pharmacist / Inventory Manager"), ("USR-READ", "ASTR", "Read-only User")]
    connection.executemany("INSERT OR IGNORE INTO pharmacy_memberships(user_id,hospital_id,role) VALUES (?,?,?)", memberships)
    for row in connection.execute("SELECT hospital_id,medicine_id,current_stock FROM inventory").fetchall():
        exists = connection.execute("SELECT 1 FROM medicine_batches WHERE hospital_id=? AND medicine_id=?", (row["hospital_id"], row["medicine_id"])).fetchone()
        if not exists:
            medicine = connection.execute("SELECT manufacture_date,expiry_date FROM medicines WHERE id=?", (row["medicine_id"],)).fetchone()
            connection.execute("INSERT INTO medicine_batches(id,hospital_id,medicine_id,batch_number,manufacture_date,expiry_date,quantity_available,source_label,is_sample,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (new_id("BAT"), row["hospital_id"], row["medicine_id"], f"DEMO-{row['hospital_id']}-{row['medicine_id']}-01",
                 medicine["manufacture_date"], medicine["expiry_date"], row["current_stock"], "Generated demo batch", 1, now_iso()))
    # Backfill an auditable movement view for sales already in the SQLite file.
    # Keep their original IDs, quantities, and timestamps intact.
    for sale in connection.execute("SELECT id,hospital_id,medicine_id,quantity,sold_at FROM sales_transactions").fetchall():
        connection.execute("INSERT OR IGNORE INTO stock_movements(id,hospital_id,medicine_id,batch_id,movement_type,quantity,note,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (sale["id"], sale["hospital_id"], sale["medicine_id"], None, "sale", sale["quantity"], "Migrated from existing sales history", "Existing record", sale["sold_at"]))
    if not connection.execute("SELECT 1 FROM notifications").fetchone():
        connection.executemany("INSERT INTO notifications(id,hospital_id,kind,severity,title,message,status,simulated,created_at) VALUES (?,?,?,?,?,?,?,?,?)", [
            (new_id("NTF"), "ASTR", "shortage", "Critical", "Norepinephrine coverage requires review", "Aster General has simulated low coverage. Review the shortage workflow before placing a real order.", "unread", 1, now_iso()),
            (new_id("NTF"), "ASTR", "shipment", "Info", "Shipment tracking is in simulation mode", "Carrier and delivery updates are presenter-controlled sample events.", "unread", 1, now_iso()),
        ])


def initialize() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_path = DB_PATH.with_suffix(".pre_medguard_2_backup.sqlite3")
    if DB_PATH.exists() and not backup_path.exists():
        shutil.copy2(DB_PATH, backup_path)
    with connect() as connection:
        connection.executescript(SCHEMA)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(medicines)")}
        if "manufacture_date" not in columns:
            connection.execute("ALTER TABLE medicines ADD COLUMN manufacture_date TEXT")
        if "expiry_date" not in columns:
            connection.execute("ALTER TABLE medicines ADD COLUMN expiry_date TEXT")
        connection.execute("UPDATE medicines SET manufacture_date=COALESCE(manufacture_date,date('now','-120 day')), expiry_date=COALESCE(expiry_date,date('now','+540 day'))")
        count = connection.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
        if count == 0:
            _seed_dataset(connection)
            record_audit(
                connection, "system.seeded", "MedGuard demo",
                "Loaded the fictional five-hospital medicine dataset.",
                "dataset", "demo-data",
                {"hospitals": 5, "medicines": 5, "supplier_count": 4, "simulated": True},
            )
        _seed_medguard_two(connection)


def reset_demo() -> None:
    with connect() as connection:
        for table in ("approvals", "recommendations", "shipments", "sales_transactions", "disruptions", "audit_events",
                      "inventory", "supplier_medicines", "suppliers", "medicines", "hospitals", "settings"):
            connection.execute("DELETE FROM " + table)
        _seed_dataset(connection)
        record_audit(
            connection, "demo.reset", "Demo operator",
            "Reset scenario, stock, shipments, settings, and event history to the deterministic baseline.",
            "dataset", "demo-data", {"simulated": True},
        )


def get_settings(connection: sqlite3.Connection) -> dict:
    values = dict(connection.execute("SELECT key,value FROM settings").fetchall())
    for key, value in DEFAULT_SETTINGS.items():
        values.setdefault(key, value)
    return values


def set_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """INSERT INTO settings(key,value) VALUES (?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )
