"""Coverage scenarios based on on-hand stock and observed demo demand."""

from __future__ import annotations

import sqlite3

from backend.db import now_iso, record_audit
from backend.agents.planning import days_of_stock, shortage_timestamp, uncovered_quantity
from backend.agents.sensing import SCENARIOS


def analyze(connection: sqlite3.Connection, disruption: dict, critical_days: int, warning_days: int) -> dict:
    scenario = SCENARIOS[disruption["scenario"]]
    arrival_days = float(disruption["impact_days"])
    rows = connection.execute(
        """SELECT h.id AS hospital_id,h.name AS hospital_name,h.city,
                  m.id AS medicine_id,m.name AS medicine_name,m.unit,
                  i.current_stock,i.daily_demand,i.safety_stock
           FROM inventory i JOIN hospitals h ON h.id=i.hospital_id
           JOIN medicines m ON m.id=i.medicine_id
           WHERE i.medicine_id=? ORDER BY i.current_stock / MAX(COALESCE(i.daily_demand,0.000001),0.000001)""",
        (disruption["medicine_id"],),
    ).fetchall()
    impacts = []
    for row in rows:
        multiplier = scenario["demand_multiplier"] if row["hospital_id"] == disruption["hospital_id"] else 1.0
        demand = float(row["daily_demand"] or 0) * multiplier
        current = float(row["current_stock"])
        days = days_of_stock(current, demand)
        gap = uncovered_quantity(current, demand, arrival_days)
        shortage_in = days if days is not None and days < arrival_days else None
        risk = "Unavailable" if days is None else (
            "Critical" if days <= critical_days else ("Watch" if days <= warning_days else "Healthy")
        )
        impacts.append({
            "hospital_id": row["hospital_id"],
            "hospital_name": row["hospital_name"],
            "city": row["city"],
            "medicine_id": row["medicine_id"],
            "medicine_name": row["medicine_name"],
            "unit": row["unit"],
            "current_stock": current,
            "daily_demand": demand,
            "baseline_daily_demand": float(row["daily_demand"] or 0),
            "safety_stock": float(row["safety_stock"]),
            "days_remaining": round(days, 2) if days is not None else None,
            "shortage_in_days": round(shortage_in, 2) if shortage_in is not None else None,
            "estimated_shortage_at": shortage_timestamp(shortage_in),
            "uncovered_quantity": int(round(gap or 0)),
            "risk": risk,
            "affected": shortage_in is not None and (gap or 0) > 0,
            "simulated": True,
        })
    affected = [entry for entry in impacts if entry["affected"]]
    result = {
        "disruption_id": disruption["id"],
        "arrival_days": arrival_days,
        "uncertain": bool(scenario["uncertain"]),
        "formula": "Days on hand = current usable stock / average daily demand. Coverage gap = max(0, demand × ETA − stock).",
        "hospitals": impacts,
        "affected_hospitals": len(affected),
        "shortage_hospitals": [
            {key: item[key] for key in ("hospital_id", "hospital_name", "days_remaining", "shortage_in_days", "uncovered_quantity", "estimated_shortage_at", "risk")}
            for item in affected
        ],
        "analyzed_at": now_iso(),
    }
    record_audit(
        connection, "agent.planning", "Scenario Planning Agent",
        "Calculated coverage and shortage exposure for " + str(len(affected)) + " hospitals.",
        "disruption", disruption["id"], result,
    )
    return result
