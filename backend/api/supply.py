"""Explainable shortage alerts, supplier procurement and hospital transfers."""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import base64
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from backend import db
from backend.api.tenant import _assert_access, require_session

router = APIRouter(prefix="/api")


class ProcurementRequest(BaseModel):
    hospital_id: str
    medicine_id: str
    supplier_id: str
    quantity: int = Field(ge=1, le=100000)


class TransferRequest(BaseModel):
    source_hospital_id: str
    medicine_id: str
    quantity: float = Field(gt=0, le=100000)
    urgency: str = Field(default="Routine", pattern="^(Routine|Urgent|Critical)$")
    note: str = Field(default="", max_length=300)


class StatusUpdate(BaseModel):
    status: str = Field(pattern="^(Approved|Rejected|In Transit|Delivered)$")
    note: str = Field(default="", max_length=300)


class RecipientRequest(BaseModel):
    recipient_email: str = Field(min_length=5, max_length=254)

    @field_validator("recipient_email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip()
        if value.count("@") != 1 or "." not in value.rsplit("@", 1)[-1]:
            raise ValueError("Enter a valid email address.")
        return value


def _iso_date(days: float) -> str | None:
    if days < 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


def _prediction_rows(connection, hospital_id: str) -> list[dict]:
    settings = db.get_settings(connection)
    rows = connection.execute(
        """SELECT i.*,h.name hospital_name,h.city,m.name medicine_name,m.unit,m.id medicine_id
           FROM inventory i JOIN hospitals h ON h.id=i.hospital_id
           JOIN medicines m ON m.id=i.medicine_id WHERE i.hospital_id=? ORDER BY m.name""",
        (hospital_id,),
    ).fetchall()
    results = []
    for row in rows:
        stock = max(0.0, float(row["current_stock"]))
        configured_demand = max(0.0, float(row["daily_demand"] or 0))
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        sales = connection.execute(
            "SELECT COALESCE(SUM(quantity),0) q,COUNT(*) n FROM sales_transactions WHERE hospital_id=? AND medicine_id=? AND sold_at>=?",
            (hospital_id, row["medicine_id"], since),
        ).fetchone()
        observed = float(sales["q"]) / 30 if int(sales["n"]) else 0.0
        demand = observed if observed > 0 else configured_demand
        safety = max(0.0, float(row["safety_stock"]))
        active_delay = connection.execute(
            "SELECT COALESCE(MAX(impact_days),0) FROM disruptions WHERE hospital_id=? AND medicine_id=? AND status='active'",
            (hospital_id, row["medicine_id"]),
        ).fetchone()[0] or 0
        lead = connection.execute(
            """SELECT MIN(sm.lead_days) FROM supplier_medicines sm JOIN suppliers s ON s.id=sm.supplier_id
               WHERE sm.medicine_id=? AND s.status NOT IN ('Unavailable','Inactive')""", (row["medicine_id"],)
        ).fetchone()[0]
        baseline_lead = float(lead if lead is not None else 7)
        delay = max(0.0, float(active_delay))
        effective_lead = baseline_lead + delay
        inbound_rows = connection.execute(
            """SELECT COALESCE(SUM(poi.quantity),0) q FROM purchase_orders po
               JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
               WHERE po.hospital_id=? AND poi.medicine_id=? AND po.status IN ('Requested','Pending','Ordered','Acknowledged','In Transit')""",
            (hospital_id, row["medicine_id"]),
        ).fetchone()
        inbound = float(inbound_rows["q"] or 0)
        days = stock / demand if demand > 0 else None
        days_to_safety = max(0.0, (stock - safety) / demand) if demand > 0 else None
        factors = []
        if stock <= safety:
            factors.append(f"Stock ({stock:g}) is at or below the minimum threshold ({safety:g}).")
        if demand > configured_demand and configured_demand > 0:
            factors.append("Recent recorded sales are above the configured demo demand baseline.")
        elif observed > 0:
            factors.append("Demand uses recorded sales from the last 30 days.")
        else:
            factors.append("Demand uses the configured demo daily-consumption estimate.")
        if delay > 0:
            factors.append(f"Active simulated disruption adds {delay:g} delivery day(s).")
        if inbound:
            factors.append(f"{inbound:g} unit(s) are on pending purchase orders; arrival is estimated using demo lead times.")
        if demand <= 0:
            factors.append("No positive daily demand is available, so stockout timing cannot be estimated.")
        days_at_replenishment = max(0, effective_lead * demand - stock)
        if inbound and days is not None and days > effective_lead:
            projected_at_arrival = max(0, stock - effective_lead * demand) + inbound
        else:
            projected_at_arrival = max(0, stock - effective_lead * demand)
        critical_days = float(settings.get("critical_days", 3))
        if demand > 0 and (stock <= 0 or days is not None and (days < effective_lead or days <= critical_days) or projected_at_arrival < safety):
            risk = "High"
        elif demand > 0 and (days <= float(settings.get("warning_days", 7)) or days_to_safety is not None and days_to_safety <= effective_lead):
            risk = "Medium"
        else:
            risk = "Low"
        action = ("Escalate immediately: request an inter-hospital transfer and contact an alternative supplier."
                  if risk == "High" else "Review supplier options and prepare a replenishment request."
                  if risk == "Medium" else "Continue routine monitoring.")
        results.append({
            "hospital_id": hospital_id, "hospital_name": row["hospital_name"], "city": row["city"],
            "medicine_id": row["medicine_id"], "medicine_name": row["medicine_name"], "unit": row["unit"],
            "current_stock": stock, "minimum_threshold": safety, "average_daily_demand": round(demand, 2),
            "days_remaining": round(days, 1) if days is not None else None,
            "days_to_minimum": round(days_to_safety, 1) if days_to_safety is not None else None,
            "supplier_lead_days": round(baseline_lead, 1), "active_disruption_delay_days": round(delay, 1),
            "effective_replenishment_days": round(effective_lead, 1), "pending_delivery_quantity": inbound,
            "predicted_shortage_date": _iso_date(days_to_safety) if days_to_safety is not None else None,
            "estimated_stockout_date": _iso_date(days) if days is not None else None,
            "risk_level": risk, "factors": factors, "recommended_action": action,
            "prediction_method": "Explainable rule-based estimate; not machine learning. Supplier lead times and disruption impacts are demo data.",
            "demand_method": "30-day recorded sales" if observed > 0 else "configured demo daily demand",
            "simulated": True,
        })
    return results


def _gmail_api_configured() -> bool:
    return all(os.getenv(name) for name in ("MEDGUARD_GMAIL_ADDRESS", "MEDGUARD_GMAIL_CLIENT_ID", "MEDGUARD_GMAIL_CLIENT_SECRET", "MEDGUARD_GMAIL_REFRESH_TOKEN"))


def _send_gmail_api(message: EmailMessage) -> None:
    form = urllib.parse.urlencode({
        "client_id": os.environ["MEDGUARD_GMAIL_CLIENT_ID"],
        "client_secret": os.environ["MEDGUARD_GMAIL_CLIENT_SECRET"],
        "refresh_token": os.environ["MEDGUARD_GMAIL_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    request = urllib.request.Request("https://oauth2.googleapis.com/token", data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        access_token = json.loads(response.read().decode())["access_token"]
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
    request = urllib.request.Request("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", data=json.dumps({"raw": raw}).encode(), headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status not in (200, 202):
            raise RuntimeError(f"Gmail API returned HTTP {response.status}.")


def _mail(subject: str, body: str, recipient: str) -> None:
    sender = os.getenv("MEDGUARD_GMAIL_ADDRESS", "").strip()
    if not sender:
        raise RuntimeError("Gmail delivery is not configured. Set MEDGUARD_GMAIL_ADDRESS on the server.")
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, sender, recipient
    message.set_content(body)
    if _gmail_api_configured():
        _send_gmail_api(message)
        return
    password = os.getenv("MEDGUARD_GMAIL_APP_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("Set Gmail API OAuth variables for hosted delivery, or MEDGUARD_GMAIL_APP_PASSWORD for a local SMTP setup.")
    with smtplib.SMTP_SSL(os.getenv("MEDGUARD_SMTP_HOST", "smtp.gmail.com"), int(os.getenv("MEDGUARD_SMTP_PORT", "465")), context=ssl.create_default_context(), timeout=15) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)


def _try_high_alert_email(connection, alert_id: str, prediction: dict) -> None:
    recipient = db.get_settings(connection).get(f"shortage_recipient_email:{prediction['hospital_id']}", "")
    if not recipient:
        return
    already_sent = connection.execute("SELECT 1 FROM email_delivery_logs WHERE alert_id=? AND kind='high_shortage' AND status='sent'", (alert_id,)).fetchone()
    if already_sent:
        return
    recent_failure = connection.execute(
        "SELECT created_at FROM email_delivery_logs WHERE alert_id=? AND kind='high_shortage' AND status='failed' ORDER BY created_at DESC LIMIT 1",
        (alert_id,),
    ).fetchone()
    retry_after = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds").replace("+00:00", "Z")
    if recent_failure and recent_failure["created_at"] >= retry_after:
        return
    body = (
        f"High medicine shortage risk: {prediction['medicine_name']} — {prediction['hospital_name']}\n\n"
        f"Current stock: {prediction['current_stock']:g} {prediction['unit']}\n"
        f"Average daily demand: {prediction['average_daily_demand']:g} {prediction['unit']}/day\n"
        f"Estimated stockout date: {prediction['estimated_stockout_date'] or 'Unavailable'}\n"
        f"Reason: {' '.join(prediction['factors'])}\nRecommended action: {prediction['recommended_action']}\n\n"
        f"Medicine details: {os.getenv('MEDGUARD_PUBLIC_URL', 'http://127.0.0.1:8000').rstrip('/')}/?view=shortages&hospital_id={prediction['hospital_id']}&medicine_id={prediction['medicine_id']}\n\n"
        "This prototype uses sample data and rule-based estimates."
    )
    status, error = "sent", None
    try:
        _mail(f"[MedGuard demo] High shortage risk: {prediction['medicine_name']}", body, recipient)
    except Exception as exc:  # delivery failure must never prevent app use
        status, error = "failed", str(exc)[:500]
    connection.execute("INSERT INTO email_delivery_logs(id,alert_id,hospital_id,recipient,kind,status,error,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (db.new_id("EML"), alert_id, prediction["hospital_id"], recipient, "high_shortage", status, error, db.now_iso()))


@router.get("/shortages/predictions")
def shortage_predictions(hospital_id: str, user: dict = Depends(require_session)):
    _assert_access(user, hospital_id)
    with db.connect() as connection:
        return _prediction_rows(connection, hospital_id)


@router.post("/shortages/evaluate/{hospital_id}")
def evaluate_shortages(hospital_id: str, user: dict = Depends(require_session)):
    _assert_access(user, hospital_id)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        predictions = _prediction_rows(connection, hospital_id)
        now = db.now_iso()
        for item in predictions:
            if item["risk_level"] == "Low":
                continue
            previous = connection.execute("SELECT * FROM shortage_alerts WHERE hospital_id=? AND medicine_id=? ORDER BY created_at DESC LIMIT 1",
                (hospital_id, item["medicine_id"])).fetchone()
            if previous and previous["status"] == "open":
                order = {"Low": 0, "Medium": 1, "High": 2}
                if order[item["risk_level"]] < order[previous["risk_level"]]:
                    continue
                alert_id = previous["id"]
                connection.execute("UPDATE shortage_alerts SET risk_level=?,current_stock=?,minimum_threshold=?,daily_demand=?,days_remaining=?,shortage_date=?,factors_json=?,recommended_action=?,updated_at=? WHERE id=?",
                    (item["risk_level"], item["current_stock"], item["minimum_threshold"], item["average_daily_demand"], item["days_remaining"], item["estimated_stockout_date"], json.dumps(item["factors"]), item["recommended_action"], now, alert_id))
                if item["risk_level"] == previous["risk_level"]:
                    continue
            elif previous:
                # Keep resolved history immutable and start a fresh alert episode.
                alert_id = db.new_id("ALT")
                connection.execute("INSERT INTO shortage_alerts(id,hospital_id,medicine_id,risk_level,current_stock,minimum_threshold,daily_demand,days_remaining,shortage_date,factors_json,recommended_action,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (alert_id, hospital_id, item["medicine_id"], item["risk_level"], item["current_stock"], item["minimum_threshold"], item["average_daily_demand"], item["days_remaining"], item["estimated_stockout_date"], json.dumps(item["factors"]), item["recommended_action"], now, now))
                connection.execute("INSERT INTO notifications(id,hospital_id,kind,severity,title,message,status,simulated,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (db.new_id("NTF"), hospital_id, "shortage", item["risk_level"], f"{item['risk_level']} shortage risk: {item['medicine_name']}", item["recommended_action"], "unread", 1, now))
            else:
                alert_id = db.new_id("ALT")
                connection.execute("INSERT INTO shortage_alerts(id,hospital_id,medicine_id,risk_level,current_stock,minimum_threshold,daily_demand,days_remaining,shortage_date,factors_json,recommended_action,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (alert_id, hospital_id, item["medicine_id"], item["risk_level"], item["current_stock"], item["minimum_threshold"], item["average_daily_demand"], item["days_remaining"], item["estimated_stockout_date"], json.dumps(item["factors"]), item["recommended_action"], now, now))
                connection.execute("INSERT INTO notifications(id,hospital_id,kind,severity,title,message,status,simulated,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (db.new_id("NTF"), hospital_id, "shortage", item["risk_level"], f"{item['risk_level']} shortage risk: {item['medicine_name']}", item["recommended_action"], "unread", 1, now))
            if item["risk_level"] == "High":
                _try_high_alert_email(connection, alert_id, item)
        db.record_audit(connection, "shortage.evaluated", user["display_name"], f"Evaluated shortage risk for {len(predictions)} medicine positions.", "hospital", hospital_id, {"alerts": sum(p["risk_level"] != "Low" for p in predictions), "simulated": True})
        return {"predictions": predictions, "alert_count": sum(p["risk_level"] != "Low" for p in predictions), "simulated": True}


@router.get("/shortages/alerts")
def shortage_alerts(hospital_id: str, include_resolved: bool = True, user: dict = Depends(require_session)):
    _assert_access(user, hospital_id)
    where, params = "a.hospital_id=?", [hospital_id]
    if not include_resolved:
        where += " AND a.status='open'"
    with db.connect() as connection:
        rows = connection.execute(f"""SELECT a.*,h.name hospital_name,m.name medicine_name,m.unit FROM shortage_alerts a
          JOIN hospitals h ON h.id=a.hospital_id JOIN medicines m ON m.id=a.medicine_id WHERE {where}
          ORDER BY CASE a.status WHEN 'open' THEN 0 ELSE 1 END,a.created_at DESC""", params).fetchall()
    return [{**dict(row), "factors": json.loads(row["factors_json"])} for row in rows]


@router.post("/shortages/alerts/{alert_id}/resolve")
def resolve_shortage_alert(alert_id: str, user: dict = Depends(require_session)):
    with db.connect() as connection:
        alert = connection.execute("SELECT * FROM shortage_alerts WHERE id=?", (alert_id,)).fetchone()
        if not alert:
            raise HTTPException(404, "Alert not found.")
        _assert_access(user, alert["hospital_id"], write=True)
        connection.execute("UPDATE shortage_alerts SET status='resolved',resolved_at=?,updated_at=? WHERE id=?", (db.now_iso(), db.now_iso(), alert_id))
        db.record_audit(connection, "shortage.resolved", user["display_name"], "Marked a shortage alert resolved.", "shortage_alert", alert_id)
    return {"status": "resolved"}


@router.get("/shortages/email-settings")
def email_settings(hospital_id: str, user: dict = Depends(require_session)):
    _assert_access(user, hospital_id)
    with db.connect() as connection:
        settings = db.get_settings(connection)
        logs = connection.execute("SELECT id,recipient,kind,status,error,created_at FROM email_delivery_logs WHERE hospital_id=? ORDER BY created_at DESC LIMIT 20", (hospital_id,)).fetchall()
    return {"recipient_email": settings.get(f"shortage_recipient_email:{hospital_id}", ""), "smtp_configured": bool(os.getenv("MEDGUARD_GMAIL_ADDRESS") and (os.getenv("MEDGUARD_GMAIL_APP_PASSWORD") or _gmail_api_configured())), "logs": [dict(row) for row in logs]}


@router.put("/shortages/email-settings")
def save_email_settings(payload: RecipientRequest, hospital_id: str = Query(...), user: dict = Depends(require_session)):
    membership = _assert_access(user, hospital_id, write=True)
    if membership["role"] not in {"Pharmacy Owner", "Platform Administrator"}:
        raise HTTPException(403, "Only a pharmacy owner can change the alert recipient.")
    with db.connect() as connection:
        db.set_setting(connection, f"shortage_recipient_email:{hospital_id}", str(payload.recipient_email))
        db.record_audit(connection, "shortage.email_recipient.updated", user["display_name"], "Updated the high-risk email recipient.", "hospital", hospital_id)
    return {"recipient_email": str(payload.recipient_email), "smtp_configured": bool(os.getenv("MEDGUARD_GMAIL_ADDRESS") and (os.getenv("MEDGUARD_GMAIL_APP_PASSWORD") or _gmail_api_configured()))}


@router.post("/shortages/test-email")
def test_shortage_email(hospital_id: str, user: dict = Depends(require_session)):
    membership = _assert_access(user, hospital_id, write=True)
    if membership["role"] not in {"Pharmacy Owner", "Platform Administrator"}:
        raise HTTPException(403, "Only a pharmacy owner can send a test notification.")
    with db.connect() as connection:
        recipient = db.get_settings(connection).get(f"shortage_recipient_email:{hospital_id}", "")
        if not recipient:
            raise HTTPException(409, "Set a recipient email address first.")
        status, error = "sent", None
        try:
            _mail("MedGuard demo test notification", "This is a test from the MedGuard shortage alert prototype. No verified medical data is included.", recipient)
        except Exception as exc:
            status, error = "failed", str(exc)[:500]
        connection.execute("INSERT INTO email_delivery_logs(id,alert_id,hospital_id,recipient,kind,status,error,created_at) VALUES (?,NULL,?,?,?,?,?,?)",
            (db.new_id("EML"), hospital_id, recipient, "test", status, error, db.now_iso()))
    if status == "failed":
        raise HTTPException(503, "Email could not be delivered. Check server SMTP configuration; the failure was logged.")
    return {"status": "sent", "recipient": recipient}


@router.get("/suppliers/alternatives")
def alternative_suppliers(hospital_id: str, medicine_id: str, quantity: int = Query(default=1, ge=1), user: dict = Depends(require_session)):
    _assert_access(user, hospital_id)
    with db.connect() as connection:
        rows = connection.execute("""SELECT s.id supplier_id,s.name supplier_name,s.city,s.reliability,s.status,
          sm.available_qty,sm.unit_price,sm.lead_days,m.unit FROM supplier_medicines sm JOIN suppliers s ON s.id=sm.supplier_id
          JOIN medicines m ON m.id=sm.medicine_id WHERE sm.medicine_id=?""", (medicine_id,)).fetchall()
    result = []
    for row in rows:
        with db.connect() as connection:
            reserved = connection.execute("""SELECT COALESCE(SUM(poi.quantity),0) FROM purchase_orders po
              JOIN purchase_order_items poi ON poi.purchase_order_id=po.id WHERE po.supplier_id=? AND poi.medicine_id=?
              AND po.status IN ('Requested','Pending','Ordered','Acknowledged','In Transit')""", (row["supplier_id"], medicine_id)).fetchone()[0]
        available = max(0.0, float(row["available_qty"]) - float(reserved or 0))
        result.append({**dict(row), "net_available_qty": available, "reserved_quantity": float(reserved or 0),
                       "available": available >= quantity, "sample_data": True,
                       "verification_label": "Demo supplier record; not externally verified", "contact_details": "Not supplied in demo data"})
    result.sort(key=lambda item: (not item["available"], item["lead_days"], item["unit_price"], -item["reliability"]))
    return result


@router.post("/procurement/requests")
def create_procurement_request(payload: ProcurementRequest, user: dict = Depends(require_session)):
    _assert_access(user, payload.hospital_id, write=True)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE OF sm" if db.is_postgres() else ""
        supplier = connection.execute("""SELECT s.*,sm.available_qty,sm.unit_price FROM suppliers s JOIN supplier_medicines sm ON sm.supplier_id=s.id
          WHERE s.id=? AND sm.medicine_id=?""" + lock, (payload.supplier_id, payload.medicine_id)).fetchone()
        if not supplier:
            raise HTTPException(404, "This supplier does not list the selected medicine in the demo catalogue.")
        committed = connection.execute("""SELECT COALESCE(SUM(poi.quantity),0) FROM purchase_orders po
          JOIN purchase_order_items poi ON poi.purchase_order_id=po.id WHERE po.supplier_id=? AND poi.medicine_id=?
          AND po.status IN ('Requested','Pending','Ordered','Acknowledged','In Transit')""", (payload.supplier_id, payload.medicine_id)).fetchone()[0]
        if float(supplier["available_qty"]) - float(committed or 0) < payload.quantity:
            raise HTTPException(409, "The listed demo quantity is insufficient for this request.")
        po_id, stamp = db.new_id("PO"), db.now_iso()
        connection.execute("INSERT INTO purchase_orders(id,hospital_id,supplier_id,status,total_quantity,created_by,created_at,updated_at,is_simulated) VALUES (?,?,?,?,?,?,?,?,1)",
            (po_id, payload.hospital_id, payload.supplier_id, "Requested", payload.quantity, user["display_name"], stamp, stamp))
        connection.execute("INSERT INTO purchase_order_items(id,purchase_order_id,medicine_id,quantity,unit_price) VALUES (?,?,?,?,?)",
            (db.new_id("POI"), po_id, payload.medicine_id, payload.quantity, supplier["unit_price"]))
        db.record_audit(connection, "procurement.requested", user["display_name"], f"Requested {payload.quantity} units from {supplier['name']}.", "purchase_order", po_id, {"simulated": True})
    return {"request_id": po_id, "status": "Requested", "simulated": True}


@router.get("/communication/dashboard")
def communication_dashboard(hospital_id: str, search: str = "", status: str = "All", user: dict = Depends(require_session)):
    _assert_access(user, hospital_id)
    with db.connect() as connection:
        requests = connection.execute("""SELECT tr.*,m.name medicine_name,m.unit,sh.name source_hospital_name,dh.name destination_hospital_name
          FROM hospital_transfer_requests tr JOIN medicines m ON m.id=tr.medicine_id
          JOIN hospitals sh ON sh.id=tr.source_hospital_id JOIN hospitals dh ON dh.id=tr.destination_hospital_id
          WHERE tr.source_hospital_id=? OR tr.destination_hospital_id=? ORDER BY tr.created_at DESC""", (hospital_id, hospital_id)).fetchall()
        procurement = connection.execute("""SELECT po.*,s.name supplier_name,poi.medicine_id,m.name medicine_name,poi.quantity,poi.unit_price
          FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
          JOIN medicines m ON m.id=poi.medicine_id WHERE po.hospital_id=? ORDER BY po.created_at DESC""", (hospital_id,)).fetchall()
        history = connection.execute("""SELECT th.*,tr.source_hospital_id,tr.destination_hospital_id,m.name medicine_name
          FROM transfer_history th JOIN hospital_transfer_requests tr ON tr.id=th.request_id JOIN medicines m ON m.id=tr.medicine_id
          WHERE tr.source_hospital_id=? OR tr.destination_hospital_id=? ORDER BY th.created_at DESC LIMIT 50""", (hospital_id, hospital_id)).fetchall()
    term = search.casefold().strip()
    req = [dict(row) for row in requests]
    po = [dict(row) for row in procurement]
    if status != "All":
        req = [row for row in req if row["status"] == status]
        po = [row for row in po if row["status"] == status]
    if term:
        req = [row for row in req if term in f"{row['medicine_name']} {row['source_hospital_name']} {row['destination_hospital_name']}".casefold()]
        po = [row for row in po if term in f"{row['medicine_name']} {row['supplier_name']}".casefold()]
    return {"transfers": req, "procurement": po, "activity": [dict(row) for row in history], "simulated": True}


@router.get("/communication/availability")
def hospital_availability(hospital_id: str, medicine_id: str, user: dict = Depends(require_session)):
    _assert_access(user, hospital_id)
    with db.connect() as connection:
        rows = connection.execute("""SELECT h.id hospital_id,h.name hospital_name,h.city,i.current_stock,i.safety_stock,i.updated_at,
          m.name medicine_name,m.unit FROM inventory i JOIN hospitals h ON h.id=i.hospital_id JOIN medicines m ON m.id=i.medicine_id
          WHERE i.medicine_id=? AND h.id<>?""", (medicine_id, hospital_id)).fetchall()
        result = []
        for row in rows:
            reserved = connection.execute("""SELECT COALESCE(SUM(quantity),0) FROM hospital_transfer_requests
              WHERE source_hospital_id=? AND medicine_id=? AND status IN ('Pending','Approved','In Transit')""",
              (row["hospital_id"], medicine_id)).fetchone()[0]
            surplus = max(0.0, float(row["current_stock"]) - float(row["safety_stock"]) - float(reserved or 0))
            if surplus > 0:
                result.append({**dict(row), "available_quantity": surplus, "sample_data": True, "distance_km": None,
                               "distance_note": "Distance is not available in the demo dataset."})
    return result


@router.get("/communication/transfers")
def list_transfers(hospital_id: str, status: str = "All", user: dict = Depends(require_session)):
    _assert_access(user, hospital_id)
    with db.connect() as connection:
        rows = connection.execute("""SELECT tr.*,m.name medicine_name,m.unit,sh.name source_hospital_name,dh.name destination_hospital_name
          FROM hospital_transfer_requests tr JOIN medicines m ON m.id=tr.medicine_id
          JOIN hospitals sh ON sh.id=tr.source_hospital_id JOIN hospitals dh ON dh.id=tr.destination_hospital_id
          WHERE tr.source_hospital_id=? OR tr.destination_hospital_id=? ORDER BY tr.created_at DESC""", (hospital_id, hospital_id)).fetchall()
    return [dict(row) for row in rows if status == "All" or row["status"] == status]


@router.post("/communication/transfers")
def request_transfer(payload: TransferRequest, hospital_id: str, user: dict = Depends(require_session)):
    _assert_access(user, hospital_id, write=True)
    if payload.source_hospital_id == hospital_id:
        raise HTTPException(422, "Choose a different supplying hospital.")
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.is_postgres() else ""
        source = connection.execute("SELECT current_stock,safety_stock FROM inventory WHERE hospital_id=? AND medicine_id=?" + lock, (payload.source_hospital_id, payload.medicine_id)).fetchone()
        if not source:
            raise HTTPException(404, "The supplying hospital has no inventory record for this medicine.")
        reserved = connection.execute("""SELECT COALESCE(SUM(quantity),0) FROM hospital_transfer_requests
          WHERE source_hospital_id=? AND medicine_id=? AND status IN ('Pending','Approved','In Transit')""", (payload.source_hospital_id, payload.medicine_id)).fetchone()[0]
        available = max(0.0, float(source["current_stock"]) - float(source["safety_stock"]) - float(reserved or 0))
        if payload.quantity > available:
            raise HTTPException(409, f"Request exceeds available surplus ({available:g} units).")
        request_id, stamp = db.new_id("TRF"), db.now_iso()
        connection.execute("INSERT INTO hospital_transfer_requests(id,medicine_id,source_hospital_id,destination_hospital_id,quantity,urgency,status,requested_by,note,created_at,updated_at) VALUES (?,?,?,?,?,?,'Pending',?,?,?,?)",
            (request_id, payload.medicine_id, payload.source_hospital_id, hospital_id, payload.quantity, payload.urgency, user["display_name"], payload.note, stamp, stamp))
        connection.execute("INSERT INTO transfer_history(id,request_id,from_status,to_status,actor,note,created_at) VALUES (?, ?, NULL,'Pending',?,?,?)", (db.new_id("TH"), request_id, user["display_name"], payload.note, stamp))
        for target_id, title in ((payload.source_hospital_id, "New stock transfer request"), (hospital_id, "Transfer request submitted")):
            connection.execute("INSERT INTO notifications(id,hospital_id,kind,severity,title,message,status,simulated,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (db.new_id("NTF"), target_id, "transfer", "Info", title, f"Transfer request {request_id} requires review.", "unread", 1, stamp))
        db.record_audit(connection, "transfer.requested", user["display_name"], f"Requested {payload.quantity:g} units from another hospital.", "transfer", request_id, {"source_hospital_id": payload.source_hospital_id, "destination_hospital_id": hospital_id})
    return {"request_id": request_id, "status": "Pending"}


@router.post("/communication/transfers/{request_id}/status")
def update_transfer_status(request_id: str, payload: StatusUpdate, user: dict = Depends(require_session)):
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.is_postgres() else ""
        transfer = connection.execute("SELECT * FROM hospital_transfer_requests WHERE id=?" + lock, (request_id,)).fetchone()
        if not transfer:
            raise HTTPException(404, "Transfer request not found.")
        source_member = next((m for m in user["memberships"] if m["hospital_id"] == transfer["source_hospital_id"]), None)
        destination_member = next((m for m in user["memberships"] if m["hospital_id"] == transfer["destination_hospital_id"]), None)
        if payload.status in {"Approved", "Rejected"}:
            if not source_member or source_member["role"] == "Read-only User":
                raise HTTPException(403, "Only an authorized staff member at the supplying hospital can approve or reject.")
        elif payload.status == "In Transit":
            if not source_member:
                raise HTTPException(403, "Only staff at the supplying hospital can dispatch this transfer.")
            if source_member["role"] == "Read-only User":
                raise HTTPException(403, "Your pharmacy role is read-only.")
        elif payload.status == "Delivered":
            if not destination_member:
                raise HTTPException(403, "Only staff at the receiving hospital can confirm delivery.")
            if destination_member["role"] == "Read-only User":
                raise HTTPException(403, "Your pharmacy role is read-only.")
        allowed = {"Pending": {"Approved", "Rejected"}, "Approved": {"In Transit"}, "In Transit": {"Delivered"}, "Rejected": set(), "Delivered": set()}
        if payload.status not in allowed.get(transfer["status"], set()):
            raise HTTPException(409, f"Cannot change transfer from {transfer['status']} to {payload.status}.")
        stamp = db.now_iso()
        if payload.status == "Approved":
            stock = connection.execute("SELECT current_stock,safety_stock FROM inventory WHERE hospital_id=? AND medicine_id=?" + lock, (transfer["source_hospital_id"], transfer["medicine_id"])).fetchone()
            reserved_elsewhere = connection.execute("""SELECT COALESCE(SUM(quantity),0) FROM hospital_transfer_requests WHERE source_hospital_id=?
              AND medicine_id=? AND id<>? AND status IN ('Pending','Approved','In Transit')""", (transfer["source_hospital_id"], transfer["medicine_id"], request_id)).fetchone()[0]
            if not stock or float(stock["current_stock"]) - float(stock["safety_stock"]) - float(reserved_elsewhere or 0) < float(transfer["quantity"]):
                raise HTTPException(409, "The supplying hospital no longer has enough unreserved surplus.")
        if payload.status == "Delivered":
            source = connection.execute("SELECT current_stock,safety_stock FROM inventory WHERE hospital_id=? AND medicine_id=?", (transfer["source_hospital_id"], transfer["medicine_id"])).fetchone()
            source_batches = connection.execute("SELECT * FROM medicine_batches WHERE hospital_id=? AND medicine_id=? AND quantity_available>0 ORDER BY expiry_date IS NULL,expiry_date,updated_at,id", (transfer["source_hospital_id"], transfer["medicine_id"])).fetchall()
            batch_total = sum(float(batch["quantity_available"]) for batch in source_batches)
            if not source or float(source["current_stock"]) - float(transfer["quantity"]) < float(source["safety_stock"]):
                raise HTTPException(409, "The supplying hospital no longer has enough unreserved stock above its minimum.")
            if batch_total < float(transfer["quantity"]):
                raise HTTPException(409, "Batch records do not contain enough transferable stock.")
            connection.execute("UPDATE inventory SET current_stock=current_stock-?,updated_at=? WHERE hospital_id=? AND medicine_id=?", (transfer["quantity"], stamp, transfer["source_hospital_id"], transfer["medicine_id"]))
            connection.execute("UPDATE inventory SET current_stock=current_stock+?,updated_at=? WHERE hospital_id=? AND medicine_id=?", (transfer["quantity"], stamp, transfer["destination_hospital_id"], transfer["medicine_id"]))
            remaining = float(transfer["quantity"])
            for batch in source_batches:
                if remaining <= 0:
                    break
                taken = min(remaining, float(batch["quantity_available"]))
                connection.execute("UPDATE medicine_batches SET quantity_available=quantity_available-?,updated_at=? WHERE id=?", (taken, stamp, batch["id"]))
                connection.execute("INSERT INTO stock_movements(id,hospital_id,medicine_id,batch_id,movement_type,quantity,note,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (db.new_id("MOV"), transfer["source_hospital_id"], transfer["medicine_id"], batch["id"], "transfer_out", taken, f"Hospital transfer {request_id}", user["display_name"], stamp))
                remaining -= taken
            destination_batch = db.new_id("BAT")
            connection.execute("INSERT INTO medicine_batches(id,hospital_id,medicine_id,batch_number,manufacture_date,expiry_date,quantity_available,source_label,is_sample,updated_at) VALUES (?,?,?,?,NULL,NULL,?,?,1,?)",
                (destination_batch, transfer["destination_hospital_id"], transfer["medicine_id"], f"TRANSFER-{request_id}", transfer["quantity"], f"Internal demo transfer from {transfer['source_hospital_id']}", stamp))
            connection.execute("INSERT INTO stock_movements(id,hospital_id,medicine_id,batch_id,movement_type,quantity,note,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (db.new_id("MOV"), transfer["destination_hospital_id"], transfer["medicine_id"], destination_batch, "transfer_in", transfer["quantity"], f"Hospital transfer {request_id}", user["display_name"], stamp))
        connection.execute("UPDATE hospital_transfer_requests SET status=?,updated_at=?,decided_by=CASE WHEN ? IN ('Approved','Rejected') THEN ? ELSE decided_by END,delivered_at=CASE WHEN ?='Delivered' THEN ? ELSE delivered_at END WHERE id=?",
            (payload.status, stamp, payload.status, user["display_name"], payload.status, stamp, request_id))
        connection.execute("INSERT INTO transfer_history(id,request_id,from_status,to_status,actor,note,created_at) VALUES (?,?,?,?,?,?,?)",
            (db.new_id("TH"), request_id, transfer["status"], payload.status, user["display_name"], payload.note, stamp))
        for hid in (transfer["source_hospital_id"], transfer["destination_hospital_id"]):
            connection.execute("INSERT INTO notifications(id,hospital_id,kind,severity,title,message,status,simulated,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (db.new_id("NTF"), hid, "transfer", "Info", f"Transfer {payload.status.lower()}", f"Transfer {request_id} status is now {payload.status}.", "unread", 1, stamp))
        db.record_audit(connection, "transfer.status", user["display_name"], f"Transfer moved to {payload.status}.", "transfer", request_id, {"status": payload.status})
    return {"request_id": request_id, "status": payload.status}


@router.get("/communication/transfers/{request_id}/history")
def transfer_history(request_id: str, user: dict = Depends(require_session)):
    with db.connect() as connection:
        transfer = connection.execute("SELECT * FROM hospital_transfer_requests WHERE id=?", (request_id,)).fetchone()
        if not transfer:
            raise HTTPException(404, "Transfer request not found.")
        if not any(m["hospital_id"] in {transfer["source_hospital_id"], transfer["destination_hospital_id"]} for m in user["memberships"]):
            raise HTTPException(403, "You do not have access to this transfer.")
        return [dict(row) for row in connection.execute("SELECT * FROM transfer_history WHERE request_id=? ORDER BY created_at", (request_id,)).fetchall()]
