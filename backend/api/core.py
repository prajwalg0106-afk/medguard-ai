import json
from fastapi import APIRouter, Query, HTTPException, Depends
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone

from backend import db
from backend.agents.planning import days_of_stock
from backend.api.common import json_load, risk_for
from backend.api.tenant import require_session, _assert_access

router = APIRouter(prefix="/api")

class PharmacyOrder(BaseModel):
    hospital_id: str
    medicine_id: str
    quantity: int = Field(ge=1, le=10000)

class SaleRequest(BaseModel):
    hospital_id: str
    medicine_id: str
    quantity: int = Field(ge=1, le=10000)


@router.get("/health")
def health():
    with db.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
    return {"status": "ok", "service": "MedGuard AI API", "database": "PostgreSQL" if db.is_postgres() else "SQLite", "hospitals": count, "mode": "simulated demo data"}


@router.get("/overview")
def overview(user: dict = Depends(require_session)):
    memberships = user.get("memberships", [])
    hospital_ids = [item["hospital_id"] for item in memberships]
    if not hospital_ids:
        raise HTTPException(403, "An active pharmacy membership is required.")
    marks = ",".join("?" for _ in hospital_ids)
    with db.connect() as connection:
        settings = db.get_settings(connection)
        rows = connection.execute(
            """SELECT i.current_stock,i.daily_demand,m.id AS medicine_id,m.name AS medicine_name,
                      m.criticality,h.name AS hospital_name
               FROM inventory i JOIN medicines m ON m.id=i.medicine_id
               JOIN hospitals h ON h.id=i.hospital_id"""
        ).fetchall()
        inventory = []
        for row in rows:
            days = days_of_stock(row["current_stock"], row["daily_demand"])
            inventory.append({
                "days_remaining": round(days, 1) if days is not None else None,
                "medicine_id": row["medicine_id"], "medicine_name": row["medicine_name"],
                "criticality": row["criticality"], "hospital_name": row["hospital_name"],
            })
        critical = [item for item in inventory if item["days_remaining"] is not None and item["days_remaining"] <= int(settings["critical_days"])]
        at_risk = {item["medicine_id"] for item in inventory if item["days_remaining"] is not None and item["days_remaining"] <= int(settings["warning_days"])}
        pending = connection.execute("SELECT COUNT(*) FROM recommendations WHERE status='Pending approval'").fetchone()[0]
        active = connection.execute("SELECT COUNT(*) FROM disruptions WHERE status='active'").fetchone()[0]
        transit = connection.execute("SELECT COUNT(*) FROM shipments WHERE status IN ('In Transit','Delayed','Dispatched','Approved')").fetchone()[0]
        hospitals_count = connection.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
        coverages = connection.execute(
            """SELECT h.id,h.name,i.current_stock,i.daily_demand FROM inventory i
               JOIN hospitals h ON h.id=i.hospital_id WHERE i.medicine_id='NOREPI' ORDER BY h.name"""
        ).fetchall()
        recent = connection.execute("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT 6").fetchall()
        events = connection.execute(
            """SELECT d.*,m.name AS medicine_name,h.name AS hospital_name
               FROM disruptions d JOIN medicines m ON m.id=d.medicine_id
               JOIN hospitals h ON h.id=d.hospital_id ORDER BY d.created_at DESC LIMIT 4"""
        ).fetchall()
        expiry_cutoff = (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()
        return {
            "hospitals_monitored": hospitals_count, "medicines_at_risk": len(at_risk),
            "critical_inventory_positions": len(critical), "active_disruptions": active,
            "pending_approvals": pending, "shipments_active": transit, "simulated": True,
            "coverage_chart": [
                {"hospital_id": row["id"], "hospital": row["name"].replace(" Hospital", ""),
                 "days": round(days_of_stock(row["current_stock"], row["daily_demand"]) or 0, 1)}
                for row in coverages
            ],
            "recent_activity": [dict(row) for row in recent],
            "recent_disruptions": [{**dict(row), "details": json_load(row["details_json"])} for row in events],
            "critical_positions": critical[:8],
            "pharmacy_name": memberships[0]["pharmacy_name"],
            "last_data_update": connection.execute("SELECT MAX(updated_at) FROM inventory WHERE hospital_id IN (" + marks + ")", hospital_ids).fetchone()[0],
            "expiry_alerts": [dict(r) for r in connection.execute("SELECT m.name AS medicine_name,m.manufacture_date,m.expiry_date,COUNT(i.hospital_id) AS locations FROM medicines m JOIN inventory i ON i.medicine_id=m.id WHERE m.expiry_date<=? GROUP BY m.id ORDER BY m.expiry_date",(expiry_cutoff,))],
            "shortage_alerts": [
                {"hospital_name": item["hospital_name"], "medicine_name": item["medicine_name"],
                 "days_remaining": item["days_remaining"], "risk": item["risk"],
                 "current_stock": item["current_stock"], "safety_stock": item["safety_stock"], "unit": item["unit"], "condition": "Unavailable" if item["current_stock"] <= 0 else "Low stock"}
                for item in [
                    {"hospital_name": r["hospital_name"], "medicine_name": r["medicine_name"],
                     "days_remaining": round(days_of_stock(r["current_stock"], r["daily_demand"]), 1) if days_of_stock(r["current_stock"], r["daily_demand"]) is not None else None,
                     "risk": risk_for(days_of_stock(r["current_stock"], r["daily_demand"]), settings),
                     "current_stock": float(r["current_stock"]), "safety_stock": float(r["safety_stock"]), "unit": r["unit"],
                     "is_shortage": float(r["current_stock"]) <= float(r["safety_stock"])}
                    for r in connection.execute("SELECT h.name AS hospital_name,m.name AS medicine_name,m.unit,i.current_stock,i.daily_demand,i.safety_stock FROM inventory i JOIN hospitals h ON h.id=i.hospital_id JOIN medicines m ON m.id=i.medicine_id").fetchall()
                ] if item["risk"] == "Critical" or item["is_shortage"]
            ],
        }


@router.post("/orders")
def place_pharmacy_order(payload: PharmacyOrder, user: dict = Depends(require_session)):
    _assert_access(user, payload.hospital_id, write=True)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        hospital = connection.execute("SELECT * FROM hospitals WHERE id=?", (payload.hospital_id,)).fetchone()
        medicine = connection.execute("SELECT * FROM medicines WHERE id=?", (payload.medicine_id,)).fetchone()
        if not hospital or not medicine:
            raise HTTPException(404, "Choose a valid pharmacy and medicine.")
        lock = " FOR UPDATE OF sm" if db.is_postgres() else ""
        stock = connection.execute("SELECT available_qty,lead_days,supplier_id,s.name,s.city FROM supplier_medicines sm JOIN suppliers s ON s.id=sm.supplier_id WHERE medicine_id=? AND available_qty>=? AND s.status='Available' ORDER BY lead_days LIMIT 1" + lock, (payload.medicine_id, payload.quantity)).fetchone()
        if not stock:
            raise HTTPException(409, "No demo supplier has enough stock for that order.")
        connection.execute("UPDATE supplier_medicines SET available_qty=available_qty-? WHERE supplier_id=? AND medicine_id=?", (payload.quantity, stock["supplier_id"], payload.medicine_id))
        shipment_id = db.new_id("SHP")
        now = db.now_iso()
        eta = max(1, int(float(stock["lead_days"])*24))
        arrival = (datetime.now(timezone.utc)+timedelta(hours=eta)).isoformat(timespec="seconds").replace("+00:00","Z")
        route = {"name": f"{stock['city']} to {hospital['city']} pharmacy route", "mode":"Temperature-controlled medical courier", "distance_km":0,"eta_hours":eta,"simulated":True}
        connection.execute("INSERT INTO shipments(id,recommendation_id,source_type,source_id,source_label,destination_id,medicine_id,quantity,status,eta_hours,expected_arrival,route_json,created_at,source_reserved) VALUES (?,NULL,'supplier',?,?,?,?,?,'Approved',?,?,?,?,1)", (shipment_id, stock["supplier_id"],stock["name"],hospital["id"],medicine["id"],payload.quantity,eta,arrival,json.dumps(route),now))
        db.record_audit(connection,"pharmacy.order",user["display_name"],f"Pharmacy order {shipment_id} created.","shipment",shipment_id,{"quantity":payload.quantity,"medicine_id":medicine["id"],"supplier_stock_reserved":True})
        return {"shipment_id":shipment_id,"status":"Approved","expected_arrival":arrival,"supplier":stock["name"]}

@router.post("/sales")
def record_sale(payload: SaleRequest, user: dict = Depends(require_session)):
    _assert_access(user, payload.hospital_id, write=True)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.is_postgres() else ""
        stock = connection.execute("SELECT current_stock FROM inventory WHERE hospital_id=? AND medicine_id=?" + lock,(payload.hospital_id,payload.medicine_id)).fetchone()
        if not stock: raise HTTPException(404,"Inventory position not found.")
        if float(stock["current_stock"]) < payload.quantity: raise HTTPException(409,"Sale exceeds available stock.")
        batches = connection.execute("SELECT * FROM medicine_batches WHERE hospital_id=? AND medicine_id=? AND quantity_available>0 ORDER BY expiry_date IS NULL,expiry_date,updated_at,id", (payload.hospital_id,payload.medicine_id)).fetchall()
        batch_total = sum(float(batch["quantity_available"]) for batch in batches)
        if batch_total < payload.quantity:
            raise HTTPException(409,"Batch records do not contain enough saleable units. Reconcile batches before recording the sale.")
        timestamp=db.now_iso(); sale_id=db.new_id("SALE")
        connection.execute("UPDATE inventory SET current_stock=current_stock-?,updated_at=? WHERE hospital_id=? AND medicine_id=?",(payload.quantity,timestamp,payload.hospital_id,payload.medicine_id))
        connection.execute("INSERT INTO sales_transactions(id,hospital_id,medicine_id,quantity,sold_at) VALUES (?,?,?,?,?)",(sale_id,payload.hospital_id,payload.medicine_id,payload.quantity,timestamp))
        remaining = float(payload.quantity)
        for batch in batches:
            if remaining <= 0: break
            consumed = min(remaining,float(batch["quantity_available"]))
            connection.execute("UPDATE medicine_batches SET quantity_available=quantity_available-?,updated_at=? WHERE id=?",(consumed,timestamp,batch["id"]))
            connection.execute("INSERT INTO stock_movements(id,hospital_id,medicine_id,batch_id,movement_type,quantity,note,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?)",(db.new_id("MOV"),payload.hospital_id,payload.medicine_id,batch["id"],"sale",consumed,"Recorded medicine sale",user["display_name"],timestamp))
            remaining -= consumed
        db.record_audit(connection,"pharmacy.sale",user["display_name"],f"Recorded a sale of {payload.quantity} units.","sale",sale_id,{"hospital_id":payload.hospital_id,"medicine_id":payload.medicine_id})
        return {"sale_id":sale_id,"quantity":payload.quantity,"remaining_stock":float(stock["current_stock"])-payload.quantity}


@router.get("/hospitals")
def hospitals(user: dict = Depends(require_session)):
    with db.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM hospitals ORDER BY name")]


@router.get("/inventory")
def inventory(search: str = "", risk: str = "All", hospital: str = "All", sort: str = "days", user: dict = Depends(require_session)):
    if hospital != "All": _assert_access(user, hospital)
    with db.connect() as connection:
        settings = db.get_settings(connection)
        rows = connection.execute(
            """SELECT h.id AS hospital_id,h.name AS hospital_name,h.city,
                      m.id AS medicine_id,m.name AS medicine_name,m.unit,m.criticality,m.manufacture_date,m.expiry_date,
                      s.name AS primary_supplier,i.current_stock,i.daily_demand,i.safety_stock,i.updated_at
               FROM inventory i JOIN hospitals h ON h.id=i.hospital_id
               JOIN medicines m ON m.id=i.medicine_id LEFT JOIN suppliers s ON s.id=m.primary_supplier_id"""
        ).fetchall()
        result, term = [], search.casefold().strip()
        for row in rows:
            days = days_of_stock(row["current_stock"], row["daily_demand"])
            current_risk = risk_for(days, settings)
            item = dict(row)
            item["days_remaining"] = round(days, 1) if days is not None else None
            item["expiry_warning"] = bool(row["expiry_date"] and row["expiry_date"] <= (datetime.now(timezone.utc).date()+timedelta(days=30)).isoformat())
            item["expiry_label"] = "Expired" if row["expiry_date"] and row["expiry_date"] < datetime.now(timezone.utc).date().isoformat() else "Expiring soon" if item["expiry_warning"] else "In date"
            item["risk"] = current_risk
            item["stock_cover"] = round(float(row["current_stock"]) / float(row["safety_stock"]), 1) if float(row["safety_stock"]) > 0 else None
            searchable = (item["hospital_name"] + " " + item["medicine_name"] + " " + item["medicine_id"] + " " + item["city"]).casefold()
            if term and term not in searchable:
                continue
            if risk != "All" and current_risk != risk:
                continue
            if hospital != "All" and item["hospital_id"] != hospital:
                continue
            result.append(item)
        if sort == "stock":
            result.sort(key=lambda item: item["current_stock"], reverse=True)
        elif sort == "hospital":
            result.sort(key=lambda item: (item["hospital_name"], item["medicine_name"]))
        else:
            result.sort(key=lambda item: (item["days_remaining"] is None, item["days_remaining"] or 0))
        return result


@router.get("/suppliers")
def suppliers(medicine_id: str | None = None, user: dict = Depends(require_session)):
    with db.connect() as connection:
        if medicine_id:
            rows = connection.execute(
                """SELECT s.id AS supplier_id,s.name AS supplier_name,s.city,s.reliability,s.status,
                          sm.unit_price,sm.available_qty,sm.lead_days,m.id AS medicine_id,m.name AS medicine_name
                   FROM supplier_medicines sm JOIN suppliers s ON s.id=sm.supplier_id
                   JOIN medicines m ON m.id=sm.medicine_id
                   WHERE sm.medicine_id=? ORDER BY sm.lead_days,s.reliability DESC""",
                (medicine_id,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT id AS supplier_id,name AS supplier_name,city,reliability,status FROM suppliers ORDER BY name").fetchall()
        return [dict(row) for row in rows]


@router.get("/disruptions")
def disruptions(user: dict = Depends(require_session)):
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT d.*,m.name AS medicine_name,h.name AS hospital_name
               FROM disruptions d JOIN medicines m ON m.id=d.medicine_id
               JOIN hospitals h ON h.id=d.hospital_id ORDER BY d.created_at DESC"""
        ).fetchall()
        return [{**dict(row), "details": json_load(row["details_json"])} for row in rows]


@router.get("/workflow/latest")
def latest_workflow(user: dict = Depends(require_session)):
    with db.connect() as connection:
        disruption = connection.execute("SELECT * FROM disruptions ORDER BY created_at DESC LIMIT 1").fetchone()
        if not disruption:
            return {"active": False}
        plan = connection.execute(
            "SELECT * FROM audit_events WHERE event_type='agent.planning' AND entity_id=? ORDER BY created_at DESC LIMIT 1",
            (disruption["id"],),
        ).fetchone()
        inventory_event = connection.execute(
            "SELECT * FROM audit_events WHERE event_type='agent.inventory' AND entity_id=? ORDER BY created_at DESC LIMIT 1",
            (disruption["id"],),
        ).fetchone()
        workflow = connection.execute(
            "SELECT * FROM audit_events WHERE event_type='workflow.completed' AND entity_id=? ORDER BY created_at DESC LIMIT 1",
            (disruption["id"],),
        ).fetchone()
        recs = connection.execute(
            """SELECT r.*,h.name AS hospital_name,dh.name AS donor_hospital_name,
                      m.name AS medicine_name,m.unit,a.id AS approval_id,a.decision
               FROM recommendations r JOIN hospitals h ON h.id=r.hospital_id
               LEFT JOIN hospitals dh ON dh.id=r.donor_hospital_id JOIN medicines m ON m.id=r.medicine_id
               LEFT JOIN approvals a ON a.recommendation_id=r.id
               WHERE r.disruption_id=? ORDER BY r.created_at""",
            (disruption["id"],),
        ).fetchall()
        return {
            "active": True,
            "disruption": {**dict(disruption), "details": json_load(disruption["details_json"])},
            "planning": json_load(plan["details_json"]) if plan else {},
            "inventory": json_load(inventory_event["details_json"]) if inventory_event else {},
            "agent_steps": json_load(workflow["details_json"]).get("agent_steps", []) if workflow else [],
            "recommendations": [{**dict(row), "details": json_load(row["details_json"])} for row in recs],
        }


@router.get("/recommendations")
def recommendations(user: dict = Depends(require_session)):
    hospital_ids = [member["hospital_id"] for member in user.get("memberships", [])]
    if not hospital_ids:
        raise HTTPException(403, "An active pharmacy membership is required.")
    marks = ",".join("?" for _ in hospital_ids)
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT r.*,h.name AS hospital_name,dh.name AS donor_hospital_name,m.name AS medicine_name,
                      m.unit,s.name AS supplier_name,a.id AS approval_id,a.decision,a.reviewer,
                      a.reason AS decision_reason
               FROM recommendations r JOIN hospitals h ON h.id=r.hospital_id
               LEFT JOIN hospitals dh ON dh.id=r.donor_hospital_id JOIN medicines m ON m.id=r.medicine_id
               LEFT JOIN suppliers s ON s.id=r.supplier_id LEFT JOIN approvals a ON a.recommendation_id=r.id
               WHERE r.hospital_id IN (""" + marks + ") ORDER BY r.created_at DESC",
            hospital_ids,
        ).fetchall()
        return [{**dict(row), "details": json_load(row["details_json"])} for row in rows]


@router.get("/approvals")
def approvals(user: dict = Depends(require_session)):
    hospital_ids = [member["hospital_id"] for member in user.get("memberships", [])]
    if not hospital_ids:
        raise HTTPException(403, "An active pharmacy membership is required.")
    marks = ",".join("?" for _ in hospital_ids)
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT a.id AS approval_id,a.decision,a.reviewer,a.reason AS decision_reason,a.created_at AS reviewed_at,
                      r.*,h.name AS hospital_name,dh.name AS donor_hospital_name,m.name AS medicine_name,
                      m.unit,s.name AS supplier_name
               FROM approvals a JOIN recommendations r ON r.id=a.recommendation_id
               JOIN hospitals h ON h.id=r.hospital_id LEFT JOIN hospitals dh ON dh.id=r.donor_hospital_id
               JOIN medicines m ON m.id=r.medicine_id LEFT JOIN suppliers s ON s.id=r.supplier_id
               WHERE r.hospital_id IN (""" + marks + ") ORDER BY CASE WHEN a.decision='pending' THEN 0 ELSE 1 END,a.created_at DESC",
            hospital_ids,
        ).fetchall()
        return [{**dict(row), "details": json_load(row["details_json"])} for row in rows]


@router.get("/shipments")
def shipments(user: dict = Depends(require_session)):
    hospital_ids = [member["hospital_id"] for member in user.get("memberships", [])]
    if not hospital_ids:
        raise HTTPException(403, "An active pharmacy membership is required.")
    marks = ",".join("?" for _ in hospital_ids)
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT sh.*,h.name AS destination_name,h.city AS destination_city,src.name AS source_hospital_name,
                      m.name AS medicine_name,m.unit,r.recommendation_type
               FROM shipments sh JOIN hospitals h ON h.id=sh.destination_id
               LEFT JOIN hospitals src ON sh.source_type='hospital' AND src.id=sh.source_id
               JOIN medicines m ON m.id=sh.medicine_id LEFT JOIN recommendations r ON r.id=sh.recommendation_id
               WHERE sh.destination_id IN (""" + marks + ") OR (sh.source_type='hospital' AND sh.source_id IN (" + marks + ")) ORDER BY CASE sh.status WHEN 'In Transit' THEN 0 WHEN 'Delayed' THEN 1 WHEN 'Approved' THEN 2 ELSE 3 END, sh.created_at DESC",
            hospital_ids + hospital_ids,
        ).fetchall()
        steps = {
            "Approved": ["Dispatched"], "Dispatched": ["In Transit"],
            "In Transit": ["Delayed", "Delivered"], "Delayed": ["In Transit", "Delivered"], "Delivered": [],
        }
        result = []
        for row in rows:
            destination_member = next((item for item in user["memberships"] if item["hospital_id"] == row["destination_id"]), None)
            source_member = next((item for item in user["memberships"] if row["source_type"] == "hospital" and item["hospital_id"] == row["source_id"]), None)
            responsible_member = source_member if row["source_type"] == "hospital" and row["status"] == "Approved" else destination_member
            advance_options = steps.get(row["status"], []) if responsible_member and responsible_member["role"] != "Read-only User" else []
            events = connection.execute("SELECT event_type,summary,created_at,details_json FROM audit_events WHERE entity_id=? AND event_type IN ('pharmacy.order','shipment.updated') ORDER BY created_at", (row["id"],)).fetchall()
            result.append({**dict(row), "route": json_load(row["route_json"]),
                           "advance_options": advance_options,
                           "timeline": [{"status": json_load(e["details_json"]).get("to_status", "Order placed" if e["event_type"] == "pharmacy.order" else e["summary"]), "summary": e["summary"], "created_at": e["created_at"]} for e in events]})
        return result


@router.get("/audit")
def audit(limit: int = Query(default=200, ge=1, le=500), user: dict = Depends(require_session)):
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "details": json_load(row["details_json"])} for row in rows]


@router.get("/analytics")
def analytics(user: dict = Depends(require_session)):
    with db.connect() as connection:
        counts = {row["decision"]: row["count"] for row in connection.execute("SELECT decision,COUNT(*) AS count FROM approvals GROUP BY decision")}
        delivered = connection.execute(
            "SELECT COUNT(*) AS count,COALESCE(SUM(quantity),0) AS quantity FROM shipments WHERE status='Delivered' AND recommendation_id IS NOT NULL"
        ).fetchone()
        total = connection.execute("SELECT COUNT(*) FROM disruptions").fetchone()[0]
        pending = connection.execute("SELECT COUNT(*) FROM approvals WHERE decision='pending'").fetchone()[0]
        audit_total = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        return {
            "disruptions_seen": total,
            "approval_decisions": sum(value for key, value in counts.items() if key != "pending"),
            "approved": counts.get("approve", 0), "rejected": counts.get("reject", 0),
            "revision_requested": counts.get("revision", 0), "pending": pending,
            "recommended_shipments_delivered": int(delivered["count"]),
            "units_recovered": int(delivered["quantity"]), "audit_events": audit_total, "simulated": True,
        }
