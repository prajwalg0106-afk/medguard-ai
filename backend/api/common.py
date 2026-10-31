import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from backend import db
from backend.agents.planning import days_of_stock


class ScenarioRequest(BaseModel):
    scenario: Literal["supplier_delay", "supplier_failure", "demand_spike"] = "supplier_delay"


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "revision"]
    reviewer: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=3, max_length=500)


class ProcurementRequest(BaseModel):
    supplier_id: str
    reviewer_context: str | None = Field(default=None, max_length=160)


class SettingsRequest(BaseModel):
    critical_days: int = Field(ge=1, le=30)
    warning_days: int = Field(ge=2, le=90)
    llm_provider: Literal["deterministic", "ollama"]
    ollama_base_url: str = Field(min_length=8, max_length=240)
    ollama_model: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def warning_above_critical(self):
        if self.warning_days <= self.critical_days:
            raise ValueError("Warning days must be greater than critical days.")
        if not self.ollama_base_url.startswith(("http://", "https://")):
            raise ValueError("Ollama URL must begin with http:// or https://.")
        return self


def json_load(value: str | None, fallback=None):
    if not value:
        return fallback if fallback is not None else {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback if fallback is not None else {}


def days_until(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=float(hours))).isoformat(timespec="seconds").replace("+00:00", "Z")


def risk_for(days: float | None, settings: dict) -> str:
    if days is None:
        return "Unavailable"
    if days <= int(settings["critical_days"]):
        return "Critical"
    if days <= int(settings["warning_days"]):
        return "Watch"
    return "Healthy"


def recommendation(connection, recommendation_id: str):
    row = connection.execute(
        """SELECT r.*,h.name AS hospital_name,h.city AS destination_city,
                  dh.name AS donor_hospital_name,dh.city AS donor_city,
                  m.name AS medicine_name,m.unit,
                  s.name AS supplier_name,s.city AS supplier_city,
                  sm.unit_price,sm.available_qty,sm.lead_days
           FROM recommendations r
           JOIN hospitals h ON h.id=r.hospital_id
           LEFT JOIN hospitals dh ON dh.id=r.donor_hospital_id
           JOIN medicines m ON m.id=r.medicine_id
           LEFT JOIN suppliers s ON s.id=r.supplier_id
           LEFT JOIN supplier_medicines sm ON sm.supplier_id=r.supplier_id AND sm.medicine_id=r.medicine_id
           WHERE r.id=?""",
        (recommendation_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Recommendation not found.")
    return row


def create_shipment(connection, rec, source_type: str, source_id: str | None,
                    source_label: str, route: dict, quantity: int) -> str:
    shipment_id = db.new_id("SHP")
    eta = float(route.get("eta_hours", 0))
    connection.execute(
        """INSERT INTO shipments
           (id,recommendation_id,source_type,source_id,source_label,destination_id,medicine_id,
            quantity,status,eta_hours,expected_arrival,route_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (shipment_id, rec["id"], source_type, source_id, source_label,
         rec["hospital_id"], rec["medicine_id"], quantity, "Approved",
         eta, days_until(eta), json.dumps(route), db.now_iso()),
    )
    return shipment_id
