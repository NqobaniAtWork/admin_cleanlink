import csv
import io
import logging

from fastapi import APIRouter, Form, Request, Response
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client, unwrap_response
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.schemas.admin_actions import AdminActionCreate
from app.services.audit_service import AuditService
from app.services.payment_service import PaymentService
from app.utils.constants import PAYMENT_GATEWAY_OPTIONS

router = APIRouter(prefix="/payments")
logger = logging.getLogger(__name__)


@router.get("")
def payments(request: Request):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "finance:view")

    rows = []
    gateways = PAYMENT_GATEWAY_OPTIONS
    error = None
    try:
        service = PaymentService(get_supabase_admin_client())
        rows = service.recent_payments()
        db_gateways = service.gateways()
        if db_gateways:
            gateways = db_gateways
    except Exception:
        logger.exception("Unable to load payments")
        error = "Payment records are unavailable."

    return templates.TemplateResponse(request, "payments.html", {"request": request, "admin": admin, "active_page": "payments", "payments": rows, "gateways": gateways, "error": error})


@router.get("/export")
def export_payments(request: Request):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "finance:view")

    try:
        rows = PaymentService(get_supabase_admin_client()).recent_payments()
    except Exception:
        rows = []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Payment ID",
        "Payer Name",
        "Payer Role",
        "Payee Name",
        "Payee Role",
        "Booking ID",
        "Subscription ID",
        "Amount",
        "Currency",
        "Method",
        "Provider",
        "Status",
        "Platform Fee",
        "Cleaner Net",
        "Paid At",
        "Created At",
    ])
    for p in rows:
        writer.writerow([
            p.get("id"),
            p.get("payer_name") or "",
            p.get("payer_role") or "",
            p.get("payee_name") or "",
            p.get("payee_role") or "",
            p.get("booking_id") or "",
            p.get("subscription_id") or "",
            p.get("amount"),
            p.get("currency", "ZAR"),
            p.get("payment_method"),
            p.get("payment_provider") or "Cash",
            p.get("status"),
            p.get("platform_fee") or 0,
            p.get("cleaner_net_amount") or 0,
            p.get("paid_at") or "",
            p.get("created_at") or "",
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payments_export.csv"},
    )


@router.post("/gateways/{code}/toggle")
def toggle_gateway(request: Request, code: str, active: bool = Form(...)):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "finance:manage")

    client = get_supabase_admin_client()
    try:
        client.table("payment_gateways").update({"active": active}).eq("code", code.upper()).execute()
        AuditService(client).record(
            AdminActionCreate(
                admin_user_id=admin.user_id,
                action="GATEWAY_UPDATED",
                target_type="payment_gateways",
                reason=f"{'Activated' if active else 'Deactivated'} gateway {code}",
                metadata={"gateway": code, "active": active},
            )
        )
    except Exception:
        logger.exception("Failed to toggle gateway %s", code)

    return RedirectResponse(url="/payments", status_code=303)


@router.post("/{payment_id}/review")
def review_payment(request: Request, payment_id: str, reason: str = Form(...)):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "finance:manage")
    PaymentService(get_supabase_admin_client()).mark_reviewed(admin.user_id, payment_id, reason)
    return RedirectResponse(url="/payments", status_code=303)