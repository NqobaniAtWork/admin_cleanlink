import csv
import io
import logging

from fastapi import APIRouter, Form, Request, Response
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.services.cleaner_service import CleanerService

router = APIRouter(prefix="/cleaners")
logger = logging.getLogger(__name__)


@router.get("")
def cleaners(request: Request):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "cleaners:view")

    rows = []
    error = None
    try:
        rows = CleanerService(get_supabase_admin_client()).list_cleaners()
    except Exception:
        logger.exception("Unable to load cleaners")
        error = "Cleaner records are unavailable."

    return templates.TemplateResponse(request, "cleaners.html", {"request": request, "admin": admin, "active_page": "cleaners", "cleaners": rows, "error": error})


@router.get("/export")
def export_cleaners(request: Request):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "cleaners:view")

    try:
        rows = CleanerService(get_supabase_admin_client()).list_cleaners()
    except Exception:
        rows = []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Cleaner ID", "User ID", "First Name", "Last Name", "Verification Status", "Subscription Status", "Availability", "Rating", "Completed Jobs"])
    for c in rows:
        p = c.get("profiles") or {}
        writer.writerow([
            c.get("id"),
            c.get("user_id"),
            p.get("first_name", ""),
            p.get("last_name", ""),
            c.get("verification_status", ""),
            c.get("subscription_status", ""),
            c.get("availability_status", ""),
            c.get("overall_rating", 0),
            c.get("completed_jobs_count", 0),
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cleaners_export.csv"},
    )


@router.post("/{cleaner_id}/suspend")
def suspend_cleaner(request: Request, cleaner_id: str, reason: str = Form("Administrative suspension")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "cleaners:manage")
    CleanerService(get_supabase_admin_client()).suspend_cleaner(admin.user_id, cleaner_id, reason)
    return RedirectResponse(url="/cleaners", status_code=303)


@router.post("/{cleaner_id}/activate")
def activate_cleaner(request: Request, cleaner_id: str, reason: str = Form("Administrative activation")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "cleaners:manage")
    CleanerService(get_supabase_admin_client()).activate_cleaner(admin.user_id, cleaner_id, reason)
    return RedirectResponse(url="/cleaners", status_code=303)


@router.get("/{cleaner_id}/documents/{document_id}/download")
def download_cleaner_document(request: Request, cleaner_id: str, document_id: str):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "cleaners:view")

    service = CleanerService(get_supabase_admin_client())
    download_url = service.get_document_download_url(cleaner_id, document_id)
    if not download_url:
        return RedirectResponse(url="/cleaners?error=Document+not+found+or+unavailable", status_code=303)

    return RedirectResponse(url=download_url, status_code=307)