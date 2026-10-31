import json

from fastapi import APIRouter, HTTPException

from backend import db
from backend.agents.logistics import route_options
from backend.api.common import ProcurementRequest

router = APIRouter(prefix="/api")


@router.post("/recommendations/procure")
def request_procurement(payload: ProcurementRequest):
    with db.connect() as connection:
        disruption = connection.execute(
            "SELECT * FROM disruptions WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not disruption:
            raise HTTPException(409, "Run a disruption scenario before requesting procurement.")
        medicine = connection.execute(
            "SELECT * FROM medicines WHERE id=?", (disruption["medicine_id"],)
        ).fetchone()
        if payload.supplier_id == medicine["primary_supplier_id"]:
            raise HTTPException(400, "Choose an alternative supplier.")
        supply = connection.execute(
            """SELECT sm.*,s.name AS supplier_name,s.city,s.reliability,s.status
               FROM supplier_medicines sm JOIN suppliers s ON s.id=sm.supplier_id
               WHERE sm.supplier_id=? AND sm.medicine_id=?""",
            (payload.supplier_id, disruption["medicine_id"]),
        ).fetchone()
        if not supply or supply["status"] != "Available":
            raise HTTPException(404, "This supplier cannot provide the selected medicine.")
        hospital = connection.execute(
            """SELECT h.*,i.current_stock,i.daily_demand FROM hospitals h
               JOIN inventory i ON i.hospital_id=h.id
               WHERE h.id=? AND i.medicine_id=?""",
            (disruption["hospital_id"], disruption["medicine_id"]),
        ).fetchone()
        quantity = min(
            int(float(supply["available_qty"])),
            max(1, int(round(float(hospital["daily_demand"]) * 2))),
        )
        if quantity <= 0:
            raise HTTPException(409, "Supplier has no available stock.")
        routes = route_options(supply["city"], hospital["city"], quantity)
        feasible = [route for route in routes if route["feasible"]]
        selected = min(feasible, key=lambda route: route["eta_hours"]) if feasible else None
        if not selected:
            raise HTTPException(409, "No route can carry the requested quantity.")
        rec_id = db.new_id("REC")
        reason = (
            f"Optional post-bridge replenishment: request {quantity} {medicine['unit']} from {supply['supplier_name']} "
            f"to rebuild approximately two days of buffer. Quote estimate is USD {float(supply['unit_price']):.2f} per unit; "
            "availability and lead time are simulated and require procurement review."
        )
        benefit = f"Adds {quantity / max(float(hospital['daily_demand']), 0.001):.1f} days of local buffer if approved and delivered."
        risk = "Supplier stock, price, route, and lead time are fictional. This procurement request remains unexecuted until approval."
        details = {
            "simulated": True,
            "current_stock": float(hospital["current_stock"]),
            "daily_demand": float(hospital["daily_demand"]),
            "quantity": quantity,
            "unit_price": float(supply["unit_price"]),
            "estimated_cost": round(float(supply["unit_price"]) * quantity, 2),
            "lead_days": float(supply["lead_days"]),
            "available_qty": float(supply["available_qty"]),
            "route_options": routes,
            "selected_route": selected,
            "supplier_reliability": float(supply["reliability"]),
            "reviewer_context": payload.reviewer_context,
        }
        connection.execute(
            """INSERT INTO recommendations
               (id,disruption_id,recommendation_type,hospital_id,donor_hospital_id,supplier_id,
                medicine_id,quantity,reason,benefit,risk,status,details_json,created_at)
               VALUES (?,?, 'procurement',?,NULL,?,?,?,?,?,?,'Pending approval',?,?)""",
            (rec_id, disruption["id"], hospital["id"], payload.supplier_id,
             medicine["id"], quantity, reason, benefit, risk, json.dumps(details), db.now_iso()),
        )
        approval_id = db.new_id("APR")
        connection.execute(
            """INSERT INTO approvals(id,recommendation_id,decision,reviewer,reason,created_at)
               VALUES (?,?,'pending',NULL,NULL,?)""",
            (approval_id, rec_id, db.now_iso()),
        )
        db.record_audit(
            connection, "agent.compliance", "Compliance and Human-in-the-Loop Agent",
            f"Alternative supplier request from {supply['supplier_name']} is waiting for authorized human approval.",
            "recommendation", rec_id,
            {"approval_id": approval_id, "quantity": quantity, "supplier_id": payload.supplier_id, "automatic_execution": False},
        )
        return {
            "id": rec_id, "approval_id": approval_id, "recommendation_type": "procurement",
            "hospital_id": hospital["id"], "hospital_name": hospital["name"],
            "medicine_id": medicine["id"], "medicine_name": medicine["name"], "unit": medicine["unit"],
            "supplier_id": payload.supplier_id, "supplier_name": supply["supplier_name"],
            "quantity": quantity, "reason": reason, "benefit": benefit, "risk": risk,
            "status": "Pending approval", "details": details,
        }
