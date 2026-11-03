import csv
import io
import logging

from fastapi import APIRouter, Form, Request, Response
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.schemas.admin_actions import AdminActionCreate
from app.services.audit_service import AuditService
from app.services.user_audit_service import UserAuditService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/users")
def users_page(request: Request, search: str = "", role: str = "", status: str = "", page: int = 1):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "users:view")
    try:
        result = UserAuditService(get_supabase_admin_client()).search_users(search=search.strip(), role=role, status=status, page=max(page, 1))
        error = None
    except Exception as exc:
        logger.exception("Unable to search users: %s", exc)
        result = {"rows": [], "count": 0, "pages": 1}
        error = "User records are temporarily unavailable. Check database connectivity."
    return templates.TemplateResponse(request, "users.html", {"request": request, "admin": admin, "active_page": "users", "result": result, "search": search, "role": role, "status": status, "page": max(page, 1), "error": error})


@router.get("/users/export")
def export_users(request: Request, search: str = "", role: str = "", status: str = ""):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "users:view")

    try:
        result = UserAuditService(get_supabase_admin_client()).search_users(search=search.strip(), role=role, status=status, page=1)
        rows = result.get("rows", [])
    except Exception:
        rows = []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["User ID", "First Name", "Last Name", "Email", "Phone", "Role", "Account Status", "Created At"])
    for u in rows:
        writer.writerow([
            u.get("id"),
            u.get("first_name", ""),
            u.get("last_name", ""),
            u.get("email", ""),
            u.get("phone", ""),
            u.get("role", ""),
            u.get("account_status", ""),
            u.get("created_at", ""),
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users_export.csv"},
    )


@router.post("/users/{user_id}/suspend")
def suspend_user(request: Request, user_id: str, reason: str = Form("Administrative suspension")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "users:view")

    client = get_supabase_admin_client()
    client.table("profiles").update({"account_status": "SUSPENDED"}).eq("id", user_id).execute()
    AuditService(client).record(
        AdminActionCreate(
            admin_user_id=admin.user_id,
            action="USER_SUSPENDED",
            target_type="profiles",
            target_id=user_id,
            reason=reason,
        )
    )
    return RedirectResponse(url="/users", status_code=303)


@router.post("/users/{user_id}/activate")
def activate_user(request: Request, user_id: str, reason: str = Form("Administrative activation")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "users:view")

    client = get_supabase_admin_client()
    client.table("profiles").update({"account_status": "ACTIVE"}).eq("id", user_id).execute()
    AuditService(client).record(
        AdminActionCreate(
            admin_user_id=admin.user_id,
            action="USER_ACTIVATED",
            target_type="profiles",
            target_id=user_id,
            reason=reason,
        )
    )
    return RedirectResponse(url="/users", status_code=303)


@router.get("/activity")
def activity_page(request: Request, event_type: str = "", actor_id: str = "", target_type: str = "", page: int = 1):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "audit:view")
    try:
        result = UserAuditService(get_supabase_admin_client()).audit_logs(event_type=event_type.strip(), actor_id=actor_id.strip(), target_type=target_type.strip(), page=max(page, 1))
        error = None
    except Exception:
        logger.exception("Unable to load activity logs")
        result, error = {"rows": [], "count": 0, "pages": 1}, "Activity logs are unavailable."
    return templates.TemplateResponse(request, "activity.html", {"request": request, "admin": admin, "active_page": "activity", "result": result, "event_type": event_type, "actor_id": actor_id, "target_type": target_type, "page": max(page, 1), "error": error})