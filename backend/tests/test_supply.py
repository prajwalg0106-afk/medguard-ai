import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import db
from backend.api import core, shipments, supply, tenant


ASTER = {"id": "USR-ASTER", "display_name": "Aster Owner", "role": "Pharmacy Owner",
         "memberships": [{"hospital_id": "ASTR", "role": "Pharmacy Owner", "pharmacy_name": "Aster General"}]}
LAKESIDE = {"id": "USR-LAKE", "display_name": "Lakeside Manager", "role": "Pharmacist / Inventory Manager",
            "memberships": [{"hospital_id": "LAKE", "role": "Pharmacist / Inventory Manager", "pharmacy_name": "Lakeside Care"}]}


class SupplyFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2])
        self.db_path_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "medguard.sqlite3")
        self.db_path_patch.start()
        db.initialize()

    def tearDown(self):
        self.db_path_patch.stop()
        self.temp.cleanup()

    def test_rule_forecast_explains_risk_and_inbound_orders(self):
        with db.connect() as connection:
            connection.execute("UPDATE inventory SET current_stock=12,daily_demand=24,safety_stock=10 WHERE hospital_id='ASTR' AND medicine_id='NOREPI'")
            prediction = next(row for row in supply._prediction_rows(connection, "ASTR") if row["medicine_id"] == "NOREPI")
            self.assertEqual(prediction["risk_level"], "High")
            self.assertEqual(prediction["days_remaining"], 0.5)
            self.assertTrue(prediction["estimated_stockout_date"])
            self.assertTrue(prediction["factors"])
            self.assertIn("rule-based", prediction["prediction_method"])

    def test_alert_generation_deduplicates_and_email_failure_is_logged(self):
        with db.connect() as connection:
            connection.execute("UPDATE inventory SET current_stock=12,daily_demand=24 WHERE hospital_id='ASTR' AND medicine_id='NOREPI'")
            db.set_setting(connection, "shortage_recipient_email:ASTR", "demo@example.com")
        with patch.object(supply, "_mail", side_effect=RuntimeError("SMTP unavailable")) as send:
            first = supply.evaluate_shortages("ASTR", ASTER)
            second = supply.evaluate_shortages("ASTR", ASTER)
        self.assertTrue(first["alert_count"])
        self.assertEqual(second["alert_count"], first["alert_count"])
        self.assertEqual(send.call_count, 1)
        with db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM shortage_alerts WHERE hospital_id='ASTR' AND medicine_id='NOREPI'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT status,error FROM email_delivery_logs WHERE kind='high_shortage'").fetchone()["status"], "failed")

    def test_supplier_comparison_marks_insufficient_and_ranks_suitable_first(self):
        rows = supply.alternative_suppliers("ASTR", "NOREPI", 100000, ASTER)
        self.assertTrue(rows)
        self.assertTrue(all(row["sample_data"] for row in rows))
        self.assertTrue(all(not row["available"] for row in rows))
        enough = supply.alternative_suppliers("ASTR", "NOREPI", 1, ASTER)
        self.assertTrue(enough[0]["available"])

    def test_transfer_approval_delivery_is_atomic_and_updates_both_stocks(self):
        with db.connect() as connection:
            before_source = connection.execute("SELECT current_stock FROM inventory WHERE hospital_id='LAKE' AND medicine_id='NOREPI'").fetchone()[0]
            before_dest = connection.execute("SELECT current_stock FROM inventory WHERE hospital_id='ASTR' AND medicine_id='NOREPI'").fetchone()[0]
        result = supply.request_transfer(supply.TransferRequest(source_hospital_id="LAKE", medicine_id="NOREPI", quantity=2), "ASTR", ASTER)
        request_id = result["request_id"]
        supply.update_transfer_status(request_id, supply.StatusUpdate(status="Approved"), LAKESIDE)
        with self.assertRaises(HTTPException) as raised:
            supply.update_transfer_status(request_id, supply.StatusUpdate(status="In Transit"), ASTER)
        self.assertEqual(raised.exception.status_code, 403)
        supply.update_transfer_status(request_id, supply.StatusUpdate(status="In Transit"), LAKESIDE)
        supply.update_transfer_status(request_id, supply.StatusUpdate(status="Delivered"), ASTER)
        with db.connect() as connection:
            after_source = connection.execute("SELECT current_stock FROM inventory WHERE hospital_id='LAKE' AND medicine_id='NOREPI'").fetchone()[0]
            after_dest = connection.execute("SELECT current_stock FROM inventory WHERE hospital_id='ASTR' AND medicine_id='NOREPI'").fetchone()[0]
            movements = connection.execute("SELECT COUNT(*) FROM stock_movements WHERE note=?", (f"Hospital transfer {request_id}",)).fetchone()[0]
        self.assertEqual(after_source, before_source - 2)
        self.assertEqual(after_dest, before_dest + 2)
        self.assertEqual(movements, 2)
        with self.assertRaises(HTTPException):
            supply.update_transfer_status(request_id, supply.StatusUpdate(status="Delivered"), ASTER)

    def test_transfer_request_cannot_exceed_unreserved_surplus(self):
        with self.assertRaises(HTTPException) as raised:
            supply.request_transfer(supply.TransferRequest(source_hospital_id="LAKE", medicine_id="NOREPI", quantity=100000), "ASTR", ASTER)
        self.assertEqual(raised.exception.status_code, 409)

    def test_procurement_request_creates_order_and_audit_record(self):
        suppliers = supply.alternative_suppliers("ASTR", "NOREPI", 1, ASTER)
        supplier_id = suppliers[0]["supplier_id"]
        result = supply.create_procurement_request(supply.ProcurementRequest(hospital_id="ASTR", medicine_id="NOREPI", supplier_id=supplier_id, quantity=1), ASTER)
        self.assertEqual(result["status"], "Requested")
        with db.connect() as connection:
            order = connection.execute("SELECT status,total_quantity,is_simulated FROM purchase_orders WHERE id=?", (result["request_id"],)).fetchone()
        self.assertEqual(order["status"], "Requested")
        self.assertEqual(order["total_quantity"], 1)
        self.assertEqual(order["is_simulated"], 1)

    def test_session_required_and_membership_scoped_write(self):
        with self.assertRaises(HTTPException) as raised:
            tenant.require_session(None)
        self.assertEqual(raised.exception.status_code, 401)
        wrong_pharmacy = {**ASTER, "memberships": LAKESIDE["memberships"]}
        with self.assertRaises(HTTPException) as raised:
            core.place_pharmacy_order(core.PharmacyOrder(hospital_id="ASTR", medicine_id="NOREPI", quantity=1), wrong_pharmacy)
        self.assertEqual(raised.exception.status_code, 403)

    def test_login_session_can_be_validated_and_revoked(self):
        result = tenant.login(tenant.LoginRequest(email="owner@aster.demo", password="AsterDemo!2026"))
        self.assertEqual(tenant.require_session(result["token"])["id"], "USR-ASTER")
        tenant.logout(result["token"], result["user"])
        with self.assertRaises(HTTPException) as raised:
            tenant.require_session(result["token"])
        self.assertEqual(raised.exception.status_code, 401)

    def test_pharmacy_order_reserves_supplier_and_delivery_updates_batch_ledger(self):
        with db.connect() as connection:
            supplier_id, available_before = connection.execute(
                "SELECT sm.supplier_id,sm.available_qty FROM supplier_medicines sm JOIN suppliers s ON s.id=sm.supplier_id WHERE sm.medicine_id='NOREPI' AND sm.available_qty>=1 AND s.status='Available' ORDER BY sm.lead_days LIMIT 1"
            ).fetchone()
            stock_before = connection.execute(
                "SELECT current_stock FROM inventory WHERE hospital_id='ASTR' AND medicine_id='NOREPI'"
            ).fetchone()[0]
        order = core.place_pharmacy_order(core.PharmacyOrder(hospital_id="ASTR", medicine_id="NOREPI", quantity=1), ASTER)
        with db.connect() as connection:
            shipment = connection.execute("SELECT * FROM shipments WHERE id=?", (order["shipment_id"],)).fetchone()
            available_after = connection.execute("SELECT available_qty FROM supplier_medicines WHERE supplier_id=? AND medicine_id='NOREPI'", (supplier_id,)).fetchone()[0]
        self.assertEqual(shipment["source_reserved"], 1)
        self.assertEqual(available_after, available_before - 1)
        shipments.advance_shipment(order["shipment_id"], "Dispatched", ASTER)
        shipments.advance_shipment(order["shipment_id"], "In Transit", ASTER)
        shipments.advance_shipment(order["shipment_id"], "Delivered", ASTER)
        with db.connect() as connection:
            stock_after = connection.execute("SELECT current_stock FROM inventory WHERE hospital_id='ASTR' AND medicine_id='NOREPI'").fetchone()[0]
            batch = connection.execute("SELECT quantity_available,source_label,is_sample FROM medicine_batches WHERE batch_number=?", (f"SHIPMENT-{order['shipment_id']}",)).fetchone()
            movement = connection.execute("SELECT movement_type,quantity FROM stock_movements WHERE note=?", (f"Shipment {order['shipment_id']} delivered",)).fetchone()
        self.assertEqual(stock_after, stock_before + 1)
        self.assertEqual(batch["quantity_available"], 1)
        self.assertEqual(batch["is_sample"], 1)
        self.assertIn("Sample", batch["source_label"])
        self.assertEqual((movement["movement_type"], movement["quantity"]), ("receipt", 1))


if __name__ == "__main__":
    unittest.main()
