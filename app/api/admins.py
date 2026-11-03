import logging

from fastapi import APIRouter, Form, Request
from pydantic import ValidationError
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.exceptions import AdminManagementError
from app.models.enums import AdminRole
from app.schemas.auth_schema import AdminCreate
from app.security.csrf import csrf_token, verify_csrf
from app.security.roles import (
    PERMISSION_LABELS,
    PERMISSIONS_CATALOG,
    ROLE_PERMISSIONS,
)
from app.services.admin_management_service import AdminManagementService

router = APIRouter(prefix="/admins")
logger = logging.getLogger(__name__)


def _render(request: Request, admin, *, error: str | None = None, status_code: int = 200):
    rows = []
    current_profile = {}
    try:
        client = get_supabase_admin_client()
        admin_service = AdminManagementService(client)
        rows = admin_service.list_admins()
        # Fetch current admin's profile data
        p_res = client.table("profiles").select("id,first_name,last_name,phone,role").eq("id", admin.user_id).execute()
        p_data = getattr(p_res, "data", None) or []
        if p_data:
            current_profile = p_data[0]
            db_name = f"{current_profile.get('first_name', '')} {current_profile.get('last_name', '')}".strip()
            if db_name:
                admin.display_name = db_name
    except Exception:
        logger.exception("Unable to load administrators")
        error = error or "Administrator records are unavailable."

    preset_map = {
        role.value: sorted(list(ROLE_PERMISSIONS.get(role, set())))
        for role in AdminRole
    }

    return templates.TemplateResponse(request, "admins.html", {
        "request": request,
        "admin": admin,
        "active_page": "admins",
        "admins": rows,
        "current_profile": current_profile,
        "roles": list(AdminRole),
        "permissions_catalog": PERMISSIONS_CATALOG,
        "permission_labels": PERMISSION_LABELS,
        "preset_map": preset_map,
        "csrf_token": csrf_token(request),
        "error": error,
        "created": request.query_params.get("created"),
        "profile_updated": request.query_params.get("profile_updated"),
        "permissions_updated": request.query_params.get("permissions_updated"),
    }, status_code=status_code)


@router.get("")
def admins_page(request: Request):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "admins:manage")
    return _render(request, admin)


@router.post("/profile")
def update_profile(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    phone: str = Form(""),
    new_password: str = Form(""),
    form_token: str = Form(...),
):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    verify_csrf(request, form_token)

    clean_first = first_name.strip()
    clean_last = last_name.strip()
    new_display = f"{clean_first} {clean_last}".strip() or "Admin"

    try:
        AdminManagementService(get_supabase_admin_client()).update_personal_details(
            user_id=admin.user_id,
            first_name=clean_first,
            last_name=clean_last,
            phone=phone,
            new_password=new_password.strip() if new_password else "",
        )
        if "admin" in request.session and isinstance(request.session["admin"], dict):
            sess = dict(request.session["admin"])
            sess["display_name"] = new_display
            request.session["admin"] = sess
    except AdminManagementError as exc:
        return _render(request, admin, error=str(exc), status_code=400)
    except Exception as exc:
        logger.exception("Failed to update personal details")
        return _render(request, admin, error=f"Unable to update profile: {exc}", status_code=400)

    return RedirectResponse(url="/admins?profile_updated=1", status_code=303)


@router.post("/{admin_id}/toggle")
def toggle_admin(request: Request, admin_id: str, active: str = Form(...)):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "admins:manage")
    is_active = active.lower() in ("true", "1", "yes")
    AdminManagementService(get_supabase_admin_client()).toggle_admin_status(admin.user_id, admin_id, is_active)
    return RedirectResponse(url="/admins", status_code=303)


@router.post("/{admin_id}/permissions")
def update_permissions_route(
    request: Request,
    admin_id: str,
    permissions: list[str] = Form(default=[]),
    role: str | None = Form(default=None),
    form_token: str = Form(...),
):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "admins:manage")
    verify_csrf(request, form_token)

    try:
        AdminManagementService(get_supabase_admin_client()).update_admin_permissions(
            actor_user_id=admin.user_id,
            admin_id=admin_id,
            permissions=permissions,
            role=role,
        )
    except AdminManagementError as exc:
        return _render(request, admin, error=str(exc), status_code=400)
    except Exception as exc:
        logger.exception("Failed to update administrator permissions")
        return _render(request, admin, error=f"Unable to update privileges: {exc}", status_code=400)

    return RedirectResponse(url="/admins?permissions_updated=1", status_code=303)


@router.post("")
def create_admin(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    role: str = Form(...),
    permissions: list[str] = Form(default=[]),
    form_token: str = Form(...),
):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "admins:manage")
    verify_csrf(request, form_token)
    try:
        payload = AdminCreate(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=role,
            permissions=permissions,
        )
        AdminManagementService(get_supabase_admin_client()).create_admin(admin.user_id, payload)
    except (ValidationError, AdminManagementError) as exc:
        message = str(exc) if isinstance(exc, AdminManagementError) else "Enter a valid email address and administrator role."
        return _render(request, admin, error=message, status_code=400)
    return RedirectResponse(url="/admins?created=1", status_code=303)