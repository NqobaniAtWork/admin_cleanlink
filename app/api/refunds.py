import csv
import io
import logging

from fastapi import APIRouter, Form, Request, Response
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.services.refund_service import RefundService

router = APIRouter(prefix="/refunds")
logger = logging.getLogger(__name__)


@router.get("")
def refunds(request: Request):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "finance:view")

    status_filter = request.query_params.get("status", "all").strip().lower()
    search = request.query_params.get("search", "").strip()
    error = request.query_params.get("error")
    success = request.query_params.get("success")
    target_payment_id = request.query_params.get("payment_id", "").strip()
    open_modal = request.query_params.get("open_modal", "false").lower() == "true" or bool(target_payment_id)

    client = get_supabase_admin_client()
    service = RefundService(client)

    metrics = {"all": 0, "pending": 0, "approved": 0, "rejected": 0, "total_refunded_zar": 0.0}
    rows = []
    refundable_payments = []

    try:
        metrics = service.count_metrics()
    except Exception:
        logger.exception("Failed to load refund metrics")

    try:
        all_rows = service.list_refunds(status=status_filter)
        if search:
            s_low = search.lower()
            rows = [
                r for r in all_rows
                if s_low in str(r.get("id", "")).lower()
                or s_low in str(r.get("payment_id", "")).lower()
                or s_low in str(r.get("booking_id", "")).lower()
                or s_low in str(r.get("requester_name", "")).lower()
                or s_low in str(r.get("reason", "")).lower()
            ]
        else:
            rows = all_rows
    except Exception:
        logger.exception("Unable to load refunds")
        error = error or "Refund records are currently unavailable."

    # Load refundable payments for modal dropdown
    try:
        refundable_payments = service.list_refundable_payments()
    except Exception:
        logger.exception("Failed to load refundable payments for modal")

    return templates.TemplateResponse(
        request,
        "refunds.html",
        {
            "request": request,
            "admin": admin,
            "active_page": "refunds",
            "refunds": rows,
            "metrics": metrics,
            "selected_status": status_filter,
            "search": search,
            "refundable_payments": refundable_payments,
            "target_payment_id": target_payment_id,
            "open_modal": open_modal,
            "error": error,
            "success": success,
        },
    )


@router.get("/export")
def export_refunds(request: Request):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "finance:view")

    service = RefundService(get_supabase_admin_client())
    try:
        rows = service.list_refunds()
    except Exception:
        rows = []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Refund ID",
        "Payment ID",
        "Booking ID",
        "Requester Name",
        "Requester Role",
        "Amount (ZAR)",
        "Status",
        "Reason",
        "Processed At",
        "Created At",
    ])
    for r in rows:
        writer.writerow([
            r.get("id"),
            r.get("payment_id"),
            r.get("booking_id") or "",
            r.get("requester_name") or "",
            r.get("requester_role") or "",
            r.get("amount"),
            r.get("status"),
            r.get("reason"),
            r.get("processed_at") or "",
            r.get("created_at") or "",
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=refunds_export.csv"},
    )


@router.post("/issue")
def issue_refund(
    request: Request,
    payment_id: str = Form(...),
    amount: float = Form(...),
    reason: str = Form(...),
    auto_approve: bool = Form(True),
):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "finance:manage")

    service = RefundService(get_supabase_admin_client())
    try:
        service.issue_refund(
            admin_user_id=admin.user_id,
            payment_id=payment_id.strip(),
            amount=amount,
            reason=reason.strip(),
            auto_approve=auto_approve,
        )
        verb = "approved and processed" if auto_approve else "queued for review"
        return RedirectResponse(
            url=f"/refunds?success=Refund of ZAR {amount:.2f} has been {verb}.",
            status_code=303,
        )
    except ValueError as val_err:
        return RedirectResponse(
            url=f"/refunds?error={val_err}",
            status_code=303,
        )
    except Exception as exc:
        logger.exception("Failed to issue refund for payment %s", payment_id)
        return RedirectResponse(
            url=f"/refunds?error=Could not issue refund: {exc}",
            status_code=303,
        )


@router.post("/{refund_id}/approve")
def approve_refund(request: Request, refund_id: str, reason: str = Form("Approved by administrator")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "finance:manage")

    service = RefundService(get_supabase_admin_client())
    try:
        service.approve_refund(admin_user_id=admin.user_id, refund_id=refund_id, reason=reason)
        return RedirectResponse(
            url="/refunds?success=Refund approved and ledger updated.",
            status_code=303,
        )
    except Exception as exc:
        logger.exception("Failed to approve refund %s", refund_id)
        return RedirectResponse(
            url=f"/refunds?error=Failed to approve refund: {exc}",
            status_code=303,
        )


@router.post("/{refund_id}/reject")
def reject_refund(request: Request, refund_id: str, reason: str = Form("Rejected by administrator")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "finance:manage")

    service = RefundService(get_supabase_admin_client())
    try:
        service.reject_refund(admin_user_id=admin.user_id, refund_id=refund_id, reason=reason)
        return RedirectResponse(
            url="/refunds?success=Refund request rejected.",
            status_code=303,
        )
    except Exception as exc:
        logger.exception("Failed to reject refund %s", refund_id)
        return RedirectResponse(
            url=f"/refunds?error=Failed to reject refund: {exc}",
            status_code=303,
        )
