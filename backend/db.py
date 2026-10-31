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
DATABASE_URL = os.environ.get("MEDGUARD_DATABASE_URL") or os.environ.get("DATABASE_URL")
SEED_PATH = BASE_DIR / "data" / "demo_data.json"


def is_postgres() -> bool:
    return bool(DATABASE_URL and DATABASE_URL.startswith(("postgres://", "postgresql://")))


class _HybridRow(dict):
    """Mapping row with SQLite-compatible numeric indexing."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def _postgres_row_factory(cursor):
    names = tuple(column.name for column in cursor.description)
    return lambda values: _HybridRow(zip(names, values))


class _PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, parameters=()):
        # The existing application uses qmark placeholders and one SQLite lock command.
        sql = sql.replace("BEGIN IMMEDIATE", "BEGIN").replace("?", "%s")
        return self._connection.execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self._connection.executemany(sql.replace("?", "%s"), parameters)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()

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
  expected_arrival TEXT, route_json TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT,
  source_reserved INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS shortage_alerts (
  id TEXT PRIMARY KEY, hospital_id TEXT NOT NULL, medicine_id TEXT NOT NULL, risk_level TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', current_stock REAL NOT NULL, minimum_threshold REAL NOT NULL DEFAULT 0, daily_demand REAL NOT NULL,
  days_remaining REAL, shortage_date TEXT, factors_json TEXT NOT NULL, recommended_action TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_shortage_alerts_scope ON shortage_alerts(hospital_id,status,created_at);
CREATE TABLE IF NOT EXISTS email_delivery_logs (
  id TEXT PRIMARY KEY, alert_id TEXT, hospital_id TEXT, recipient TEXT NOT NULL, kind TEXT NOT NULL,
  status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hospital_transfer_requests (
  id TEXT PRIMARY KEY, medicine_id TEXT NOT NULL, source_hospital_id TEXT NOT NULL,
  destination_hospital_id TEXT NOT NULL, quantity REAL NOT NULL, urgency TEXT NOT NULL,
  status TEXT NOT NULL, requested_by TEXT NOT NULL, decided_by TEXT, note TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS transfer_history (
  id TEXT PRIMARY KEY, request_id TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
  actor TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL,
  FOREIGN KEY(request_id) REFERENCES hospital_transfer_requests(id)
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
def connect() -> Iterator:
    if is_postgres():
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL is configured but psycopg is not installed. Install backend/requirements.txt.") from exc
        raw_connection = psycopg.connect(DATABASE_URL, row_factory=_postgres_row_factory, connect_timeout=10)
        connection = _PostgresConnection(raw_connection)
    else:
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
        connection.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT DO NOTHING", (key, value))


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
    connection.executemany("INSERT INTO pharmacy_memberships(user_id,hospital_id,role) VALUES (?,?,?) ON CONFLICT DO NOTHING", memberships)
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
        connection.execute("INSERT INTO stock_movements(id,hospital_id,medicine_id,batch_id,movement_type,quantity,note,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (sale["id"], sale["hospital_id"], sale["medicine_id"], None, "sale", sale["quantity"], "Migrated from existing sales history", "Existing record", sale["sold_at"]))
    if not connection.execute("SELECT 1 FROM notifications").fetchone():
        connection.executemany("INSERT INTO notifications(id,hospital_id,kind,severity,title,message,status,simulated,created_at) VALUES (?,?,?,?,?,?,?,?,?)", [
            (new_id("NTF"), "ASTR", "shortage", "Critical", "Norepinephrine coverage requires review", "Aster General has simulated low coverage. Review the shortage workflow before placing a real order.", "unread", 1, now_iso()),
            (new_id("NTF"), "ASTR", "shipment", "Info", "Shipment tracking is in simulation mode", "Carrier and delivery updates are presenter-controlled sample events.", "unread", 1, now_iso()),
        ])


def initialize(seed_if_empty: bool = True) -> None:
    if not is_postgres():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        backup_path = DB_PATH.with_suffix(".pre_medguard_2_backup.sqlite3")
        if DB_PATH.exists() and not backup_path.exists():
            shutil.copy2(DB_PATH, backup_path)
    with connect() as connection:
        if is_postgres():
            for statement in SCHEMA.split(";"):
                statement = statement.strip()
                if statement and not statement.upper().startswith("PRAGMA"):
                    connection.execute(statement)
            def columns(table):
                return {row[0] for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", (table,)
                ).fetchall()}
        else:
            connection.executescript(SCHEMA)
            def columns(table):
                return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        email_columns = columns("email_delivery_logs")
        if "hospital_id" not in email_columns:
            connection.execute("ALTER TABLE email_delivery_logs ADD COLUMN hospital_id TEXT")
        medicine_columns = columns("medicines")
        if "manufacture_date" not in medicine_columns:
            connection.execute("ALTER TABLE medicines ADD COLUMN manufacture_date TEXT")
        if "expiry_date" not in medicine_columns:
            connection.execute("ALTER TABLE medicines ADD COLUMN expiry_date TEXT")
        shipment_columns = columns("shipments")
        if "source_reserved" not in shipment_columns:
            connection.execute("ALTER TABLE shipments ADD COLUMN source_reserved INTEGER NOT NULL DEFAULT 0")
        alert_columns = columns("shortage_alerts")
        if "minimum_threshold" not in alert_columns:
            connection.execute("ALTER TABLE shortage_alerts ADD COLUMN minimum_threshold REAL NOT NULL DEFAULT 0")
        manufacture = (datetime.now(timezone.utc)-timedelta(days=120)).date().isoformat()
        expiry = (datetime.now(timezone.utc)+timedelta(days=540)).date().isoformat()
        connection.execute("UPDATE medicines SET manufacture_date=COALESCE(manufacture_date,?), expiry_date=COALESCE(expiry_date,?)", (manufacture, expiry))
        count = connection.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
        if count == 0 and seed_if_empty:
            _seed_dataset(connection)
            record_audit(
                connection, "system.seeded", "MedGuard demo",
                "Loaded the fictional five-hospital medicine dataset.",
                "dataset", "demo-data",
                {"hospitals": 5, "medicines": 5, "supplier_count": 4, "simulated": True},
            )
        if seed_if_empty:
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
