import json
import logging
from typing import Any

from fastapi import APIRouter, Form, Request
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.repositories.settings_repository import SettingsRepository
from app.schemas.admin_actions import AdminActionCreate
from app.services.audit_service import AuditService

router = APIRouter(prefix="/settings")
logger = logging.getLogger(__name__)


@router.get("")
def settings_page(request: Request, updated: str = ""):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "settings:view")

    rows = []
    error = None
    try:
        rows = SettingsRepository(get_supabase_admin_client()).list_settings()
        for r in rows:
            val = r.get("value_json")
            if r.get("value_type") == "JSON" or isinstance(val, (dict, list)):
                r["edit_value"] = json.dumps(val)
            else:
                r["edit_value"] = str(val) if val is not None else ""
    except Exception:
        logger.exception("Unable to load settings")
        error = "System settings are unavailable."

    return templates.TemplateResponse(request, "settings.html", {"request": request, "admin": admin, "active_page": "settings", "settings": rows, "error": error, "updated": updated})


@router.post("/update")
def update_setting_route(request: Request, key: str = Form(...), value: str = Form(...), value_type: str = Form("STRING")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "settings:manage")

    client = get_supabase_admin_client()
    parsed_value: Any = value
    vtype = value_type.upper()
    try:
        if vtype == "NUMBER":
            parsed_value = float(value) if "." in value else int(value)
        elif vtype == "BOOLEAN":
            parsed_value = value.strip().lower() in ("true", "1", "yes")
        elif vtype == "JSON":
            parsed_value = json.loads(value)
        else:
            parsed_value = value
    except Exception:
        parsed_value = value

    try:
        SettingsRepository(client).update_setting(key, parsed_value)
        AuditService(client).record(
            AdminActionCreate(
                admin_user_id=admin.user_id,
                action="SETTING_UPDATED",
                target_type="system_settings",
                reason=f"Updated setting {key}",
                metadata={"key": key, "new_value": str(parsed_value)},
            )
        )
    except Exception as exc:
        logger.exception("Failed to update setting %s", key)

    return RedirectResponse(url=f"/settings?updated={key}", status_code=303)