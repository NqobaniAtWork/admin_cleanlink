import logging

from fastapi import APIRouter, Form, Request
from starlette.responses import RedirectResponse, Response

from app.core.supabase import get_supabase_admin_client, unwrap_response
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.repositories.admin_repository import AdminRepository
from app.repositories.verification_repository import VerificationRepository
from app.services.verification_service import VerificationService

router = APIRouter(prefix="/verification")
client_router = APIRouter(prefix="/client-verification")
logger = logging.getLogger(__name__)


@router.get("")
def verification_queue(request: Request, force_entity: str | None = None):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    if not (admin.has_permission("verification:review") or admin.has_permission("identity_verification.view")):
        ensure_permission(admin, "verification:review")

    rows = []
    error = request.query_params.get("error")
    success = request.query_params.get("success")
    status_param = request.query_params.get("status")
    entity_param = (force_entity or request.query_params.get("entity", "cleaner")).lower()
    if entity_param not in ["cleaner", "client"]:
        entity_param = "cleaner"
    search_param = request.query_params.get("search", "").strip()

    service = VerificationService(get_supabase_admin_client())

    cleaner_counts = {"all": 0, "pending": 0, "verified": 0, "rejected": 0}
    client_counts = {"all": 0, "pending": 0, "verified": 0, "rejected": 0, "unverified": 0}

    try:
        cleaner_counts = service.count_metrics()
    except Exception:
        logger.exception("Unable to load cleaner verification metrics")

    try:
        client_counts = service.count_client_metrics()
    except Exception:
        logger.exception("Unable to load client verification metrics")

    if entity_param == "client":
        if not status_param:
            selected_status = "pending" if client_counts.get("pending", 0) > 0 else "all"
        else:
            selected_status = status_param.lower()
        try:
            rows = service.list_client_applications(status_filter=selected_status, search=search_param)
        except Exception:
            logger.exception("Unable to load client verification applications")
            error = error or "Client verification queue is unavailable."
    else:
        if not status_param:
            selected_status = "pending" if cleaner_counts.get("pending", 0) > 0 else "all"
        else:
            selected_status = status_param.lower()
        try:
            rows = service.list_applications(status_filter=selected_status)
        except Exception:
            logger.exception("Unable to load cleaner verification applications")
            error = error or "Cleaner verification queue is unavailable."

    counts = {
        "all": client_counts.get("all", 0) if entity_param == "client" else cleaner_counts.get("all", 0),
        "pending": client_counts.get("pending", 0) if entity_param == "client" else cleaner_counts.get("pending", 0),
        "verified": client_counts.get("verified", 0) if entity_param == "client" else cleaner_counts.get("verified", 0),
        "rejected": client_counts.get("rejected", 0) if entity_param == "client" else cleaner_counts.get("rejected", 0),
        "unverified": client_counts.get("unverified", 0),
        "cleaners_all": cleaner_counts.get("all", 0),
        "cleaners_pending": cleaner_counts.get("pending", 0),
        "cleaners_verified": cleaner_counts.get("verified", 0),
        "cleaners_rejected": cleaner_counts.get("rejected", 0),
        "clients_all": client_counts.get("all", 0),
        "clients_pending": client_counts.get("pending", 0),
        "clients_verified": client_counts.get("verified", 0),
        "clients_rejected": client_counts.get("rejected", 0),
        "clients_unverified": client_counts.get("unverified", 0),
    }

    return templates.TemplateResponse(
        request,
        "verification.html",
        {
            "request": request,
            "admin": admin,
            "active_page": "client_verification" if entity_param == "client" else "verification",
            "entity": entity_param,
            "applications": rows,
            "counts": counts,
            "cleaner_counts": cleaner_counts,
            "client_counts": client_counts,
            "selected_status": selected_status,
            "search": search_param,
            "error": error,
            "success": success,
        },
    )


@router.get("/clients")
def clients_verification_queue(request: Request):
    return verification_queue(request, force_entity="client")


@client_router.get("")
def client_verification_page(request: Request):
    return verification_queue(request, force_entity="client")


# --- Cleaner Verification Actions ---

@router.post("/{application_id}/approve")
def approve(request: Request, application_id: str, cleaner_id: str = Form(...), reason: str | None = Form(default=None)):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "verification:review")

    client = get_supabase_admin_client()
    admin_prof_id = admin.admin_profile_id
    if not admin_prof_id:
        try:
            prof = AdminRepository(client).get_active_admin_by_user_id(admin.user_id)
            if prof:
                admin_prof_id = str(prof["id"])
        except Exception:
            pass

    try:
        VerificationService(client).approve_cleaner(admin.user_id, admin_prof_id, application_id, cleaner_id, reason)
    except Exception as exc:
        logger.exception(f"Error approving cleaner {cleaner_id} on app {application_id}: {exc}")
        return RedirectResponse(url="/verification?entity=cleaner&error=Unable+to+complete+cleaner+approval.+Please+try+again.", status_code=303)

    return RedirectResponse(url="/verification?entity=cleaner&success=Cleaner+verified+and+approved+successfully.", status_code=303)


@router.post("/{application_id}/reject")
def reject(request: Request, application_id: str, cleaner_id: str = Form(...), reason: str = Form("Rejected after administrative review")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "verification:review")

    client = get_supabase_admin_client()
    admin_prof_id = admin.admin_profile_id
    if not admin_prof_id:
        try:
            prof = AdminRepository(client).get_active_admin_by_user_id(admin.user_id)
            if prof:
                admin_prof_id = str(prof["id"])
        except Exception:
            pass

    try:
        VerificationService(client).reject_cleaner(admin.user_id, admin_prof_id, application_id, cleaner_id, reason)
    except Exception as exc:
        logger.exception(f"Error rejecting cleaner {cleaner_id} on app {application_id}: {exc}")
        return RedirectResponse(url="/verification?entity=cleaner&error=Unable+to+complete+cleaner+rejection.+Please+try+again.", status_code=303)

    return RedirectResponse(url="/verification?entity=cleaner&success=Cleaner+application+has+been+rejected.", status_code=303)


@router.post("/documents/{document_id}/review")
def review_document(
    request: Request,
    document_id: str,
    cleaner_id: str = Form(...),
    status: str = Form(...),
    reason: str | None = Form(default=None),
):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "verification:review")

    client = get_supabase_admin_client()
    admin_prof_id = admin.admin_profile_id
    if not admin_prof_id:
        try:
            prof = AdminRepository(client).get_active_admin_by_user_id(admin.user_id)
            if prof:
                admin_prof_id = str(prof["id"])
        except Exception:
            pass

    try:
        VerificationService(client).review_document(
            admin.user_id,
            admin_prof_id,
            document_id,
            cleaner_id,
            status,
            reason,
        )
    except Exception as exc:
        logger.exception(f"Error reviewing document {document_id}: {exc}")
        return RedirectResponse(url="/verification?entity=cleaner&error=Unable+to+update+document+status.", status_code=303)

    return RedirectResponse(url="/verification?entity=cleaner&success=Document+status+updated+successfully.", status_code=303)


@router.get("/documents/{document_id}/view")
def view_verification_document(request: Request, document_id: str):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "verification:review")

    client = get_supabase_admin_client()
    try:
        res = client.table("cleaner_verification_documents").select("storage_path, original_file_name, mime_type").eq("id", document_id).limit(1).execute()
        rows = unwrap_response(res) or []
        if rows and rows[0].get("storage_path"):
            storage_path = rows[0]["storage_path"]
            filename = rows[0].get("original_file_name") or "document.pdf"
            mime_type = rows[0].get("mime_type") or "application/pdf"
            
            try:
                storage_client = getattr(client, "storage", None)
                if storage_client:
                    file_bytes = storage_client.from_("cleaner-verification-documents").download(storage_path)
                    if file_bytes:
                        return Response(
                            content=file_bytes,
                            media_type=mime_type,
                            headers={
                                "Content-Disposition": f'inline; filename="{filename}"',
                                "Content-Type": mime_type,
                                "Cache-Control": "private, max-age=3600",
                                "X-Content-Type-Options": "nosniff",
                            }
                        )
            except Exception as stream_err:
                logger.warning(f"Direct stream failed for document {document_id}, falling back to signed URL: {stream_err}")

            repo = VerificationRepository(client)
            url = repo.get_signed_url("cleaner-verification-documents", storage_path, expires_in=3600)
            if url:
                return RedirectResponse(url=url, status_code=307)
    except Exception as exc:
        logger.warning(f"Error generating view for document {document_id}: {exc}")

    return RedirectResponse(url="/verification?entity=cleaner&error=Document+preview+unavailable", status_code=303)


@router.get("/documents/{document_id}/download")
def download_verification_document(request: Request, document_id: str):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "verification:review")

    client = get_supabase_admin_client()
    try:
        res = client.table("cleaner_verification_documents").select("storage_path, original_file_name, mime_type").eq("id", document_id).limit(1).execute()
        rows = unwrap_response(res) or []
        if rows and rows[0].get("storage_path"):
            storage_path = rows[0]["storage_path"]
            filename = rows[0].get("original_file_name") or "document"
            mime_type = rows[0].get("mime_type") or "application/octet-stream"

            try:
                storage_client = getattr(client, "storage", None)
                if storage_client:
                    file_bytes = storage_client.from_("cleaner-verification-documents").download(storage_path)
                    if file_bytes:
                        return Response(
                            content=file_bytes,
                            media_type=mime_type,
                            headers={
                                "Content-Disposition": f'attachment; filename="{filename}"',
                                "Content-Type": mime_type,
                            }
                        )
            except Exception as stream_err:
                logger.warning(f"Direct stream download failed: {stream_err}")

            repo = VerificationRepository(client)
            url = repo.get_signed_url("cleaner-verification-documents", storage_path, expires_in=3600)
            if url:
                return RedirectResponse(url=url, status_code=307)
    except Exception as exc:
        logger.warning(f"Error generating download for document {document_id}: {exc}")

    return RedirectResponse(url="/verification?entity=cleaner&error=Document+download+unavailable", status_code=303)


# --- Client Verification Actions ---

@router.post("/clients/{user_id}/approve")
def approve_client(
    request: Request,
    user_id: str,
    reason: str | None = Form(default=None),
    evidence_reference: str | None = Form(default=None),
):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    if not (admin.has_permission("verification:review") or admin.has_permission("identity_verification.approve")):
        ensure_permission(admin, "verification:review")

    client = get_supabase_admin_client()
    admin_prof_id = admin.admin_profile_id
    if not admin_prof_id:
        try:
            prof = AdminRepository(client).get_active_admin_by_user_id(admin.user_id)
            if prof:
                admin_prof_id = str(prof["id"])
        except Exception:
            pass

    try:
        VerificationService(client).approve_client(
            admin.user_id,
            admin_prof_id,
            user_id,
            reason=reason or "Client identity verified and approved by administrator",
            evidence_reference=evidence_reference,
        )
    except Exception as exc:
        logger.exception(f"Error approving client {user_id}: {exc}")
        return RedirectResponse(
            url="/verification?entity=client&error=Unable+to+complete+client+approval.+Please+try+again.",
            status_code=303,
        )

    return RedirectResponse(
        url="/verification?entity=client&success=Client+verified+and+approved+successfully.",
        status_code=303,
    )


@router.post("/clients/{user_id}/reject")
def reject_client(
    request: Request,
    user_id: str,
    reason: str = Form("Rejected after administrative review"),
):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    if not (admin.has_permission("verification:review") or admin.has_permission("identity_verification.reject")):
        ensure_permission(admin, "verification:review")

    client = get_supabase_admin_client()
    admin_prof_id = admin.admin_profile_id
    if not admin_prof_id:
        try:
            prof = AdminRepository(client).get_active_admin_by_user_id(admin.user_id)
            if prof:
                admin_prof_id = str(prof["id"])
        except Exception:
            pass

    try:
        VerificationService(client).reject_client(
            admin.user_id,
            admin_prof_id,
            user_id,
            reason=reason,
        )
    except Exception as exc:
        logger.exception(f"Error rejecting client {user_id}: {exc}")
        return RedirectResponse(
            url="/verification?entity=client&error=Unable+to+complete+client+rejection.+Please+try+again.",
            status_code=303,
        )

    return RedirectResponse(
        url="/verification?entity=client&success=Client+verification+has+been+rejected.",
        status_code=303,
    )


@router.post("/clients/{user_id}/reset")
def reset_client(
    request: Request,
    user_id: str,
    reason: str | None = Form(default=None),
):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "verification:review")

    client = get_supabase_admin_client()
    admin_prof_id = admin.admin_profile_id
    if not admin_prof_id:
        try:
            prof = AdminRepository(client).get_active_admin_by_user_id(admin.user_id)
            if prof:
                admin_prof_id = str(prof["id"])
        except Exception:
            pass

    try:
        VerificationService(client).reset_client(
            admin.user_id,
            admin_prof_id,
            user_id,
            reason=reason or "Client verification status reset to unverified",
        )
    except Exception as exc:
        logger.exception(f"Error resetting client {user_id}: {exc}")
        return RedirectResponse(
            url="/verification?entity=client&error=Unable+to+reset+client+verification.",
            status_code=303,
        )

    return RedirectResponse(
        url="/verification?entity=client&success=Client+verification+reset+to+unverified.",
        status_code=303,
    )
