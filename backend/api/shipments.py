from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from backend import db
from backend.api.common import days_until
from backend.api.tenant import require_session

router = APIRouter(prefix="/api")


@router.post("/shipments/{shipment_id}/advance")
def advance_shipment(
    shipment_id: str,
    status: Literal["Dispatched", "In Transit", "Delayed", "Delivered"] = Query(...),
    user: dict = Depends(require_session),
):
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.is_postgres() else ""
        shipment = connection.execute("SELECT * FROM shipments WHERE id=?" + lock, (shipment_id,)).fetchone()
        if not shipment:
            raise HTTPException(404, "Shipment not found.")
        destination_member = next((item for item in user["memberships"] if item["hospital_id"] == shipment["destination_id"]), None)
        source_member = next((item for item in user["memberships"] if shipment["source_type"] == "hospital" and item["hospital_id"] == shipment["source_id"]), None)
        member = source_member if status == "Dispatched" and shipment["source_type"] == "hospital" else destination_member
        if not member:
            raise HTTPException(403, "You need access to the hospital responsible for this shipment step.")
        if member["role"] == "Read-only User":
            raise HTTPException(403, "Your pharmacy role is read-only.")
        allowed = {
            "Approved": {"Dispatched"}, "Dispatched": {"In Transit"},
            "In Transit": {"Delayed", "Delivered"}, "Delayed": {"In Transit", "Delivered"},
            "Delivered": set(),
        }
        if status not in allowed.get(shipment["status"], set()):
            raise HTTPException(409, f"Cannot move shipment from {shipment['status']} to {status}.")
        quantity = float(shipment["quantity"])
        stamp = db.now_iso()
        if status == "Dispatched":
            if shipment["source_type"] == "hospital":
                donor = connection.execute("SELECT current_stock,safety_stock FROM inventory WHERE hospital_id=? AND medicine_id=?", (shipment["source_id"],shipment["medicine_id"])).fetchone()
                if not donor or float(donor["current_stock"]) - quantity < float(donor["safety_stock"]):
                    raise HTTPException(409, "Dispatch would breach the donor's minimum safety stock.")
                batches = connection.execute("SELECT * FROM medicine_batches WHERE hospital_id=? AND medicine_id=? AND quantity_available>0 ORDER BY expiry_date IS NULL,expiry_date,updated_at,id", (shipment["source_id"],shipment["medicine_id"])).fetchall()
                if sum(float(batch["quantity_available"]) for batch in batches) < quantity:
                    raise HTTPException(409, "Batch records do not contain enough units to dispatch this transfer.")
                connection.execute("UPDATE inventory SET current_stock=current_stock-?,updated_at=? WHERE hospital_id=? AND medicine_id=?", (quantity,stamp,shipment["source_id"],shipment["medicine_id"]))
                remaining = quantity
                for batch in batches:
                    if remaining <= 0: break
                    taken = min(remaining,float(batch["quantity_available"]))
                    connection.execute("UPDATE medicine_batches SET quantity_available=quantity_available-?,updated_at=? WHERE id=?", (taken,stamp,batch["id"]))
                    connection.execute("INSERT INTO stock_movements(id,hospital_id,medicine_id,batch_id,movement_type,quantity,note,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (db.new_id("MOV"),shipment["source_id"],shipment["medicine_id"],batch["id"],"transfer_out",taken,f"Shipment {shipment_id} dispatch",user["display_name"],stamp))
                    remaining -= taken
            elif not shipment["source_reserved"]:
                supply = connection.execute("SELECT available_qty FROM supplier_medicines WHERE supplier_id=? AND medicine_id=?", (shipment["source_id"],shipment["medicine_id"])).fetchone()
                if not supply or float(supply["available_qty"]) < quantity:
                    raise HTTPException(409, "Supplier availability no longer covers this order.")
                connection.execute("UPDATE supplier_medicines SET available_qty=available_qty-? WHERE supplier_id=? AND medicine_id=?", (quantity,shipment["source_id"],shipment["medicine_id"]))
            connection.execute("UPDATE shipments SET status=? WHERE id=?", (status,shipment_id))
        elif status == "Delayed":
            new_eta = float(shipment["eta_hours"]) + 12
            connection.execute("UPDATE shipments SET status=?,eta_hours=?,expected_arrival=? WHERE id=?", (status,new_eta,days_until(new_eta),shipment_id))
        elif status == "Delivered":
            if not connection.execute("SELECT 1 FROM inventory WHERE hospital_id=? AND medicine_id=?", (shipment["destination_id"],shipment["medicine_id"])).fetchone():
                raise HTTPException(404, "Destination inventory record was not found.")
            connection.execute("UPDATE inventory SET current_stock=current_stock+?,updated_at=? WHERE hospital_id=? AND medicine_id=?", (quantity,stamp,shipment["destination_id"],shipment["medicine_id"]))
            batch_id = db.new_id("BAT")
            connection.execute("INSERT INTO medicine_batches(id,hospital_id,medicine_id,batch_number,manufacture_date,expiry_date,quantity_available,source_label,is_sample,updated_at) VALUES (?,?,?,?,NULL,NULL,?,?,1,?)", (batch_id,shipment["destination_id"],shipment["medicine_id"],f"SHIPMENT-{shipment_id}",quantity,f"Sample stock receipt from {shipment['source_label']}",stamp))
            connection.execute("INSERT INTO stock_movements(id,hospital_id,medicine_id,batch_id,movement_type,quantity,note,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (db.new_id("MOV"),shipment["destination_id"],shipment["medicine_id"],batch_id,"receipt",quantity,f"Shipment {shipment_id} delivered",user["display_name"],stamp))
            connection.execute("UPDATE shipments SET status=?,delivered_at=?,eta_hours=0,expected_arrival=? WHERE id=?", (status,stamp,stamp,shipment_id))
            if shipment["recommendation_id"]:
                connection.execute("UPDATE recommendations SET status='Delivered' WHERE id=?", (shipment["recommendation_id"],))
                rec = connection.execute("SELECT disruption_id FROM recommendations WHERE id=?", (shipment["recommendation_id"],)).fetchone()
                unfinished = connection.execute("SELECT COUNT(*) FROM recommendations WHERE disruption_id=? AND status<>'Delivered'", (rec["disruption_id"],)).fetchone()[0]
                if unfinished == 0:
                    connection.execute("UPDATE disruptions SET status='recovered' WHERE id=?", (rec["disruption_id"],))
        else:
            connection.execute("UPDATE shipments SET status=? WHERE id=?", (status,shipment_id))
        db.record_audit(connection, "shipment.updated", user["display_name"], f"Shipment {shipment_id} advanced from {shipment['status']} to {status}.", "shipment", shipment_id, {"from_status":shipment["status"],"to_status":status,"quantity":quantity,"simulated":True})
        return {"shipment_id":shipment_id,"status":status,"message":"Shipment and audit log updated."}
