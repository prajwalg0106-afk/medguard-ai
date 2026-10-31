from fastapi import APIRouter, HTTPException

from backend import db
from backend.api.common import DecisionRequest, create_shipment, json_load, recommendation

router = APIRouter(prefix="/api")


@router.post("/approvals/{approval_id}/decision")
def decide(approval_id: str, payload: DecisionRequest):
    with db.connect() as connection:
        approval = connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not approval:
            raise HTTPException(404, "Approval request not found.")
        if approval["decision"] != "pending":
            raise HTTPException(409, "This request has already received a decision.")
        rec = recommendation(connection, approval["recommendation_id"])
        details = json_load(rec["details_json"])
        shipment_id = None
        if payload.decision == "approve":
            quantity = int(rec["quantity"])
            if quantity <= 0:
                raise HTTPException(400, "Approval quantity must be greater than zero.")
            route = details.get("selected_route")
            if not route or not route.get("feasible"):
                raise HTTPException(409, "No feasible route is available for this quantity.")
            if rec["recommendation_type"] == "transfer":
                donor = connection.execute(
                    "SELECT current_stock,safety_stock FROM inventory WHERE hospital_id=? AND medicine_id=?",
                    (rec["donor_hospital_id"], rec["medicine_id"]),
                ).fetchone()
                if not donor or float(donor["current_stock"]) - quantity < float(donor["safety_stock"]):
                    raise HTTPException(409, "Current donor stock cannot meet this transfer while preserving safety stock.")
                shipment_id = create_shipment(
                    connection, rec, "hospital", rec["donor_hospital_id"],
                    rec["donor_hospital_name"], route, quantity,
                )
            else:
                if not rec["supplier_id"] or float(rec["available_qty"] or 0) < quantity:
                    raise HTTPException(409, "Supplier quantity is no longer available.")
                shipment_id = create_shipment(
                    connection, rec, "supplier", rec["supplier_id"],
                    rec["supplier_name"], route, quantity,
                )
            recommendation_status = "Approved"
        elif payload.decision == "reject":
            recommendation_status = "Rejected"
        else:
            recommendation_status = "Revision Requested"
        reviewer = payload.reviewer.strip()
        reason = payload.reason.strip()
        connection.execute(
            "UPDATE approvals SET decision=?,reviewer=?,reason=?,created_at=? WHERE id=?",
            (payload.decision, reviewer, reason, db.now_iso(), approval_id),
        )
        connection.execute(
            "UPDATE recommendations SET status=? WHERE id=?",
            (recommendation_status, rec["id"]),
        )
        verb = {"approve": "approved", "reject": "rejected", "revision": "requested revision for"}[payload.decision]
        summary = f"{reviewer} {verb} the {rec['recommendation_type']} action for {rec['hospital_name']}: {reason}"
        db.record_audit(
            connection, "human.decision", reviewer, summary,
            "recommendation", rec["id"], {
                "decision": payload.decision, "reason": reason,
                "approval_id": approval_id, "shipment_id": shipment_id,
                "execution_requires_approval": True,
            },
        )
        return {
            "approval_id": approval_id, "decision": payload.decision,
            "recommendation_status": recommendation_status, "shipment_id": shipment_id,
            "message": "Decision recorded in the audit log.",
        }
