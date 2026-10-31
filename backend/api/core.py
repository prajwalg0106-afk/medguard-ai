import json
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone

from backend import db
from backend.agents.planning import days_of_stock
from backend.api.common import json_load, risk_for

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
    return {"status": "ok", "service": "MedGuard AI API", "database": "SQLite", "hospitals": count, "mode": "simulated demo data"}


@router.get("/overview")
def overview():
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
            "pharmacy_name": "Aster General Hospital Pharmacy",
            "last_data_update": connection.execute("SELECT MAX(updated_at) FROM inventory").fetchone()[0],
            "expiry_alerts": [dict(r) for r in connection.execute("SELECT m.name AS medicine_name,m.manufacture_date,m.expiry_date,COUNT(i.hospital_id) AS locations FROM medicines m JOIN inventory i ON i.medicine_id=m.id WHERE m.expiry_date<=date('now','+30 day') GROUP BY m.id ORDER BY m.expiry_date")],
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
def place_pharmacy_order(payload: PharmacyOrder):
    with db.connect() as connection:
        hospital = connection.execute("SELECT * FROM hospitals WHERE id=?", (payload.hospital_id,)).fetchone()
        medicine = connection.execute("SELECT * FROM medicines WHERE id=?", (payload.medicine_id,)).fetchone()
        if not hospital or not medicine:
            raise HTTPException(404, "Choose a valid pharmacy and medicine.")
        stock = connection.execute("SELECT available_qty,lead_days,supplier_id,s.name,s.city FROM supplier_medicines sm JOIN suppliers s ON s.id=sm.supplier_id WHERE medicine_id=? AND available_qty>=? ORDER BY lead_days LIMIT 1", (payload.medicine_id, payload.quantity)).fetchone()
        if not stock:
            raise HTTPException(409, "No demo supplier has enough stock for that order.")
        shipment_id = db.new_id("SHP")
        now = db.now_iso()
        eta = max(1, int(float(stock["lead_days"])*24))
        arrival = (datetime.now(timezone.utc)+timedelta(hours=eta)).isoformat(timespec="seconds").replace("+00:00","Z")
        route = {"name": f"{stock['city']} to {hospital['city']} pharmacy route", "mode":"Temperature-controlled medical courier", "distance_km":0,"eta_hours":eta,"simulated":True}
        connection.execute("INSERT INTO shipments(id,recommendation_id,source_type,source_id,source_label,destination_id,medicine_id,quantity,status,eta_hours,expected_arrival,route_json,created_at) VALUES (?,NULL,'supplier',?,?,?,?,?,'Approved',?,?,?,?)", (shipment_id, stock["supplier_id"],stock["name"],hospital["id"],medicine["id"],payload.quantity,eta,arrival,json.dumps(route),now))
        db.record_audit(connection,"pharmacy.order","Pharmacy user",f"Pharmacy order {shipment_id} created.","shipment",shipment_id,{"quantity":payload.quantity,"medicine_id":medicine["id"]})
        return {"shipment_id":shipment_id,"status":"Approved","expected_arrival":arrival,"supplier":stock["name"]}

@router.post("/sales")
def record_sale(payload: SaleRequest):
    with db.connect() as connection:
        stock = connection.execute("SELECT current_stock FROM inventory WHERE hospital_id=? AND medicine_id=?",(payload.hospital_id,payload.medicine_id)).fetchone()
        if not stock: raise HTTPException(404,"Inventory position not found.")
        if float(stock["current_stock"]) < payload.quantity: raise HTTPException(409,"Sale exceeds available stock.")
        timestamp=db.now_iso(); sale_id=db.new_id("SALE")
        connection.execute("UPDATE inventory SET current_stock=current_stock-?,updated_at=? WHERE hospital_id=? AND medicine_id=?",(payload.quantity,timestamp,payload.hospital_id,payload.medicine_id))
        connection.execute("INSERT INTO sales_transactions(id,hospital_id,medicine_id,quantity,sold_at) VALUES (?,?,?,?,?)",(sale_id,payload.hospital_id,payload.medicine_id,payload.quantity,timestamp))
        db.record_audit(connection,"pharmacy.sale","Pharmacy user",f"Recorded a sale of {payload.quantity} units.","sale",sale_id,{"hospital_id":payload.hospital_id,"medicine_id":payload.medicine_id})
        return {"sale_id":sale_id,"quantity":payload.quantity,"remaining_stock":float(stock["current_stock"])-payload.quantity}


@router.get("/hospitals")
def hospitals():
    with db.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM hospitals ORDER BY name")]


@router.get("/inventory")
def inventory(search: str = "", risk: str = "All", hospital: str = "All", sort: str = "days"):
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
def suppliers(medicine_id: str | None = None):
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
def disruptions():
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT d.*,m.name AS medicine_name,h.name AS hospital_name
               FROM disruptions d JOIN medicines m ON m.id=d.medicine_id
               JOIN hospitals h ON h.id=d.hospital_id ORDER BY d.created_at DESC"""
        ).fetchall()
        return [{**dict(row), "details": json_load(row["details_json"])} for row in rows]


@router.get("/workflow/latest")
def latest_workflow():
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
def recommendations():
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT r.*,h.name AS hospital_name,dh.name AS donor_hospital_name,m.name AS medicine_name,
                      m.unit,s.name AS supplier_name,a.id AS approval_id,a.decision,a.reviewer,
                      a.reason AS decision_reason
               FROM recommendations r JOIN hospitals h ON h.id=r.hospital_id
               LEFT JOIN hospitals dh ON dh.id=r.donor_hospital_id JOIN medicines m ON m.id=r.medicine_id
               LEFT JOIN suppliers s ON s.id=r.supplier_id LEFT JOIN approvals a ON a.recommendation_id=r.id
               ORDER BY r.created_at DESC"""
        ).fetchall()
        return [{**dict(row), "details": json_load(row["details_json"])} for row in rows]


@router.get("/approvals")
def approvals():
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT a.id AS approval_id,a.decision,a.reviewer,a.reason AS decision_reason,a.created_at AS reviewed_at,
                      r.*,h.name AS hospital_name,dh.name AS donor_hospital_name,m.name AS medicine_name,
                      m.unit,s.name AS supplier_name
               FROM approvals a JOIN recommendations r ON r.id=a.recommendation_id
               JOIN hospitals h ON h.id=r.hospital_id LEFT JOIN hospitals dh ON dh.id=r.donor_hospital_id
               JOIN medicines m ON m.id=r.medicine_id LEFT JOIN suppliers s ON s.id=r.supplier_id
               ORDER BY CASE WHEN a.decision='pending' THEN 0 ELSE 1 END,a.created_at DESC"""
        ).fetchall()
        return [{**dict(row), "details": json_load(row["details_json"])} for row in rows]


@router.get("/shipments")
def shipments():
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT sh.*,h.name AS destination_name,h.city AS destination_city,
                      m.name AS medicine_name,m.unit,r.recommendation_type
               FROM shipments sh JOIN hospitals h ON h.id=sh.destination_id
               JOIN medicines m ON m.id=sh.medicine_id LEFT JOIN recommendations r ON r.id=sh.recommendation_id
               ORDER BY CASE sh.status WHEN 'In Transit' THEN 0 WHEN 'Delayed' THEN 1 WHEN 'Approved' THEN 2 ELSE 3 END,
                        sh.created_at DESC"""
        ).fetchall()
        steps = {
            "Approved": ["Dispatched"], "Dispatched": ["In Transit"],
            "In Transit": ["Delayed", "Delivered"], "Delayed": ["In Transit", "Delivered"], "Delivered": [],
        }
        result = []
        for row in rows:
            events = connection.execute("SELECT event_type,summary,created_at,details_json FROM audit_events WHERE entity_id=? AND event_type IN ('pharmacy.order','shipment.updated') ORDER BY created_at", (row["id"],)).fetchall()
            result.append({**dict(row), "route": json_load(row["route_json"]),
                           "advance_options": steps.get(row["status"], []),
                           "timeline": [{"status": json_load(e["details_json"]).get("to_status", "Order placed" if e["event_type"] == "pharmacy.order" else e["summary"]), "summary": e["summary"], "created_at": e["created_at"]} for e in events]})
        return result


@router.get("/audit")
def audit(limit: int = Query(default=200, ge=1, le=500)):
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "details": json_load(row["details_json"])} for row in rows]


@router.get("/analytics")
def analytics():
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
