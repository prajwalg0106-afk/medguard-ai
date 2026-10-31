from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from backend import db
from backend.api.common import days_until

router = APIRouter(prefix="/api")


@router.post("/shipments/{shipment_id}/advance")
def advance_shipment(
    shipment_id: str,
    status: Literal["Dispatched", "In Transit", "Delayed", "Delivered"] = Query(...),
):
    with db.connect() as connection:
        shipment = connection.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
        if not shipment:
            raise HTTPException(404, "Shipment not found.")
        allowed = {
            "Approved": {"Dispatched"},
            "Dispatched": {"In Transit"},
            "In Transit": {"Delayed", "Delivered"},
            "Delayed": {"In Transit", "Delivered"},
            "Delivered": set(),
        }
        if status not in allowed.get(shipment["status"], set()):
            raise HTTPException(409, f"Cannot move shipment from {shipment['status']} to {status}.")
        if status == "Dispatched":
            quantity = float(shipment["quantity"])
            if shipment["source_type"] == "hospital":
                donor = connection.execute(
                    "SELECT current_stock,safety_stock FROM inventory WHERE hospital_id=? AND medicine_id=?",
                    (shipment["source_id"], shipment["medicine_id"]),
                ).fetchone()
                if not donor or float(donor["current_stock"]) - quantity < float(donor["safety_stock"]):
                    raise HTTPException(409, "Dispatch would breach the donor's minimum safety stock.")
                connection.execute(
                    """UPDATE inventory SET current_stock=current_stock-?,updated_at=?
                       WHERE hospital_id=? AND medicine_id=?""",
                    (quantity, db.now_iso(), shipment["source_id"], shipment["medicine_id"]),
                )
            elif shipment["source_type"] == "supplier":
                supply = connection.execute(
                    "SELECT available_qty FROM supplier_medicines WHERE supplier_id=? AND medicine_id=?",
                    (shipment["source_id"], shipment["medicine_id"]),
                ).fetchone()
                if not supply or float(supply["available_qty"]) < quantity:
                    raise HTTPException(409, "Supplier availability no longer covers this order.")
                connection.execute(
                    "UPDATE supplier_medicines SET available_qty=available_qty-? WHERE supplier_id=? AND medicine_id=?",
                    (quantity, shipment["source_id"], shipment["medicine_id"]),
                )
        if status == "Delayed":
            new_eta = float(shipment["eta_hours"]) + 12
            connection.execute(
                "UPDATE shipments SET status=?,eta_hours=?,expected_arrival=? WHERE id=?",
                (status, new_eta, days_until(new_eta), shipment_id),
            )
        elif status == "Delivered":
            connection.execute(
                """UPDATE inventory SET current_stock=current_stock+?,updated_at=?
                   WHERE hospital_id=? AND medicine_id=?""",
                (shipment["quantity"], db.now_iso(), shipment["destination_id"], shipment["medicine_id"]),
            )
            connection.execute(
                "UPDATE shipments SET status=?,delivered_at=?,eta_hours=0,expected_arrival=? WHERE id=?",
                (status, db.now_iso(), db.now_iso(), shipment_id),
            )
            if shipment["recommendation_id"]:
                connection.execute("UPDATE recommendations SET status='Delivered' WHERE id=?", (shipment["recommendation_id"],))
                rec = connection.execute(
                    "SELECT disruption_id FROM recommendations WHERE id=?",
                    (shipment["recommendation_id"],),
                ).fetchone()
                unfinished = connection.execute(
                    "SELECT COUNT(*) FROM recommendations WHERE disruption_id=? AND status<>'Delivered'",
                    (rec["disruption_id"],),
                ).fetchone()[0]
                if unfinished == 0:
                    connection.execute("UPDATE disruptions SET status='recovered' WHERE id=?", (rec["disruption_id"],))
        else:
            connection.execute("UPDATE shipments SET status=? WHERE id=?", (status, shipment_id))
        db.record_audit(
            connection, "shipment.updated", "Logistics operator",
            f"Shipment {shipment_id} advanced from {shipment['status']} to {status}.",
            "shipment", shipment_id, {
                "from_status": shipment["status"], "to_status": status,
                "quantity": float(shipment["quantity"]), "simulated": True,
            },
        )
        return {"shipment_id": shipment_id, "status": status, "message": "Shipment and audit log updated."}
