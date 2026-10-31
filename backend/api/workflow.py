from fastapi import APIRouter, HTTPException

from backend import db
from backend.agents.orchestrator import run_disruption_workflow
from backend.agents.llm import explain_with_ollama
from backend.api.common import ScenarioRequest

router = APIRouter(prefix="/api")


@router.post("/disruptions/simulate")
def simulate(payload: ScenarioRequest):
    with db.connect() as connection:
        active = connection.execute("SELECT id FROM disruptions WHERE status='active' LIMIT 1").fetchone()
        if active:
            raise HTTPException(409, "A demo disruption is already active. Reset the demo before running another scenario.")
        workflow = run_disruption_workflow(connection, payload.scenario)
        settings = db.get_settings(connection)
        if settings.get("llm_provider") == "ollama":
            narrative = explain_with_ollama(
                settings.get("ollama_base_url", "http://localhost:11434"),
                settings.get("ollama_model", "llama3.2"),
                {
                    "event": workflow["disruption"]["title"],
                    "simulated": True,
                    "affected_hospitals": workflow["planning"]["shortage_hospitals"],
                    "transfer_recommendations": workflow["inventory"]["recommendations"],
                },
            )
            workflow["narrative"] = narrative
            workflow["narrative_provider"] = "ollama" if narrative else "deterministic fallback"
        return workflow


@router.post("/demo/reset")
def reset_demo():
    with db.connect() as connection:
        has_sales = connection.execute("SELECT EXISTS(SELECT 1 FROM sales_transactions)").fetchone()[0]
        has_pharmacy_orders = connection.execute("SELECT EXISTS(SELECT 1 FROM audit_events WHERE event_type='pharmacy.order')").fetchone()[0]
    if has_sales or has_pharmacy_orders:
        raise HTTPException(409, "Reset paused to preserve recorded pharmacy sales and orders. Remove them only through an explicitly authorized data-management action.")
    db.reset_demo()
    return {"status": "reset", "message": "Scenario and fictional data restored to the deterministic baseline."}
