from fastapi import APIRouter, HTTPException, Depends

from backend import db
from backend.agents.llm import check_ollama
from backend.api.common import SettingsRequest
from backend.api.tenant import require_session

router = APIRouter(prefix="/api")


@router.get("/settings")
def get_settings(user: dict = Depends(require_session)):
    with db.connect() as connection:
        values = db.get_settings(connection)
        return {
            "critical_days": int(values["critical_days"]),
            "warning_days": int(values["warning_days"]),
            "llm_provider": values["llm_provider"],
            "ollama_base_url": values["ollama_base_url"],
            "ollama_model": values["ollama_model"],
            "mode": "simulated demo data",
        }


@router.put("/settings")
def update_settings(payload: SettingsRequest, user: dict = Depends(require_session)):
    if user.get("role") != "Platform Administrator":
        raise HTTPException(403,"Only a platform administrator can change shared system settings.")
    with db.connect() as connection:
        for key, value in payload.model_dump().items():
            db.set_setting(connection, key, str(value))
        db.record_audit(
            connection, "settings.updated", user["display_name"],
            "Updated risk thresholds or optional local AI provider settings.",
            "settings", "demo-settings",
            {"critical_days": payload.critical_days, "warning_days": payload.warning_days,
             "llm_provider": payload.llm_provider, "api_keys_saved": False},
        )
    return get_settings(user)


@router.post("/settings/check")
def check_provider(user: dict = Depends(require_session)):
    current = get_settings(user)
    if current["llm_provider"] != "ollama":
        return {
            "provider": "deterministic", "connected": True,
            "message": "Deterministic local agent mode is ready; no API key or network service is required.",
        }
    result = check_ollama(current["ollama_base_url"])
    return {
        "provider": "ollama", **result,
        "message": ("Ollama is reachable on this machine." if result["connected"]
                    else "Ollama is not reachable. The workflow will continue with deterministic calculations."),
    }
