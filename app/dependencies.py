from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client, unwrap_response
from app.models.enums import AdminRole
from app.schemas.auth_schema import AdminSession


def get_admin_client() -> Any:
    return get_supabase_admin_client()


def current_admin(request: Request) -> AdminSession | None:
    data = request.session.get("admin")
    if not data:
        return None
    try:
        session = AdminSession.model_validate(data)
    except ValueError:
        request.session.clear()
        return None

    # Cookie identity is only a lookup key. Database state authorizes every request.
    try:
        client = get_supabase_admin_client()
        response = client.table("admin_profiles").select(
            "*,profiles(first_name,last_name,account_status)"
        ).eq("user_id", session.user_id).limit(1).execute()
        rows = unwrap_response(response) or []
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Administrator access could not be verified.") from exc
    row = rows[0] if rows else None
    profile = (row or {}).get("profiles") or {}
    if not row or not row.get("active") or profile.get("account_status") != "ACTIVE":
        request.session.clear()
        return None
    # Always reload delegated permissions. Never retain an older cookie grant.
    custom_permissions = row.get("custom_permissions")
    if custom_permissions is None:
        try:
            response = client.auth.admin.get_user_by_id(session.user_id)
            user = getattr(response, "user", response)
            metadata = getattr(user, "app_metadata", {}) or {}
            custom_permissions = metadata.get("permissions", []) if isinstance(metadata, dict) else []
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Administrator permissions could not be verified.") from exc
    if not isinstance(custom_permissions, list) or not all(isinstance(item, str) for item in custom_permissions):
        request.session.clear()
        return None
    try:
        session = AdminSession.model_validate({
            **session.model_dump(),
            "admin_profile_id": str(row["id"]),
            "role": row["admin_role"],
            "custom_permissions": custom_permissions,
            "display_name": " ".join(part for part in [profile.get("first_name"), profile.get("last_name")] if part).strip() or "Admin",
        })
    except (ValueError, KeyError):
        request.session.clear()
        return None
    request.session["admin"] = session.model_dump(mode="json")

    return session


def require_admin_api(request: Request) -> AdminSession:
    admin = current_admin(request)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required.")
    return admin


def require_permission(permission: str) -> Callable[[Request], AdminSession]:
    def dependency(request: Request) -> AdminSession:
        admin = require_admin_api(request)
        if not admin.has_permission(permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied.")
        return admin

    return dependency


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


def ensure_permission(admin: AdminSession, permission: str) -> None:
    if not admin.has_permission(permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied.")