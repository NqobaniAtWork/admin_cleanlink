import logging

from fastapi import APIRouter, Form, HTTPException, Request
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.security.csrf import csrf_token, verify_csrf
from app.security.roles import has_permission
from app.services.client_identity_service import ClientIdentityAdminService

router = APIRouter(prefix="/identity-verification")
logger = logging.getLogger(__name__)
templates.env.globals["has_permission"] = has_permission


@router.get("")
def identity_queue(request: Request, page: int = 0):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "identity_verification.view")
    error = None
    data = {"states": [], "reviews": [], "attempts": [], "deletions": []}
    try:
        data = ClientIdentityAdminService(get_supabase_admin_client()).overview(max(0, min(page, 10000)))
    except Exception:
        logger.warning("Client identity metadata could not be loaded")
        error = "Client account checks are unavailable. Check the reviewed migration and service configuration."
    return templates.TemplateResponse(request, "client_identity.html", {
        "request": request, "admin": admin, "active_page": "client_identity", "error": error,
        "csrf_token": csrf_token(request), "page": max(0, page), **data,
    })


@router.post("/{review_id}/decision")
def decide_identity(request: Request, review_id: str, decision: str = Form(...), evidence_reference: str = Form(...),
                    reason: str = Form(...), form_token: str = Form(...), confirmed: bool = Form(False)):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "identity_verification.review")
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "Choose approve or reject.")
    ensure_permission(admin, f"identity_verification.{decision}")
    verify_csrf(request, form_token)
    if not confirmed:
        raise HTTPException(400, "Confirm that the approved account-check process was completed.")
    try:
        ClientIdentityAdminService(get_supabase_admin_client()).decide(review_id, admin.user_id, decision == "approve", evidence_reference, reason)
    except ValueError:
        raise HTTPException(400, "Enter a valid review and evidence reference and a decision reason.") from None
    except Exception:
        logger.warning("Client identity decision was not completed")
        raise HTTPException(409, "The decision could not be completed. Refresh the case; it may already be decided or inactive.") from None
    return RedirectResponse("/identity-verification", status_code=303)


@router.post("/deletions/{task_id}/confirm")
def confirm_provider_purge(request: Request, task_id: str, confirmation_reference: str = Form(...),
                           form_token: str = Form(...), confirmed: bool = Form(False)):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "identity_verification.approve")
    verify_csrf(request, form_token)
    if not confirmed:
        raise HTTPException(400, "Confirm that the processor actually completed deletion.")
    try:
        ClientIdentityAdminService(get_supabase_admin_client()).confirm_deletion(task_id, admin.user_id, confirmation_reference)
    except ValueError:
        raise HTTPException(400, "Enter a valid task and provider confirmation reference.") from None
    except Exception:
        logger.warning("Provider deletion confirmation was not recorded")
        raise HTTPException(409, "Deletion confirmation could not be recorded. Refresh the task and retry.") from None
    return RedirectResponse("/identity-verification", status_code=303)
