import logging

from fastapi import APIRouter, Request

from app.core.supabase import get_supabase_admin_client
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.services.dashboard_service import DashboardService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
def dashboard(request: Request):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "dashboard:view")

    metrics = []
    error = None
    try:
        metrics = DashboardService(get_supabase_admin_client()).metrics()
    except Exception:
        logger.exception("Unable to load dashboard metrics")
        error = "Dashboard metrics are unavailable. Check Supabase connectivity and service credentials."

    return templates.TemplateResponse(request, "dashboard.html",
        {"request": request, "admin": admin, "active_page": "dashboard", "metrics": metrics, "error": error},
    )