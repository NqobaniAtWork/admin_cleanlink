import csv
import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request, Response
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client, unwrap_response
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.schemas.admin_actions import AdminActionCreate
from app.services.audit_service import AuditService

router = APIRouter(prefix="/bookings")
logger = logging.getLogger(__name__)


@router.get("")
def bookings(request: Request, status: str = "", search: str = ""):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "bookings:view")

    rows = []
    error = None
    try:
        client = get_supabase_admin_client()
        query = client.table("bookings").select(
            "id,client_id,cleaner_id,service_type_id,status,scheduled_start,scheduled_end,gross_amount,platform_fee,cleaner_net_amount,currency,payment_method,created_at"
        )
        if status:
            query = query.eq("status", status)
        response = query.order("scheduled_start", desc=True).limit(100).execute()
        rows = unwrap_response(response) or []

        # Collect unique foreign IDs for batch lookup
        client_profile_ids = list({str(r["client_id"]) for r in rows if r.get("client_id")})
        cleaner_profile_ids = list({str(r["cleaner_id"]) for r in rows if r.get("cleaner_id")})
        service_ids = list({str(r["service_type_id"]) for r in rows if r.get("service_type_id")})

        client_map = {}
        if client_profile_ids:
            try:
                c_res = client.table("profiles").select("id,first_name,last_name,phone").in_("id", client_profile_ids).execute()
                for p in unwrap_response(c_res) or []:
                    fn = (p.get("first_name") or "").strip()
                    ln = (p.get("last_name") or "").strip()
                    client_map[str(p["id"])] = f"{fn} {ln}".strip() or p.get("phone") or "Client"
            except Exception:
                pass

        cleaner_map = {}
        if cleaner_profile_ids:
            try:
                cl_res = client.table("cleaner_profiles").select("id,profiles(first_name,last_name)").in_("id", cleaner_profile_ids).execute()
                for cp in unwrap_response(cl_res) or []:
                    prof = cp.get("profiles") or {}
                    fn = (prof.get("first_name") or "").strip()
                    ln = (prof.get("last_name") or "").strip()
                    cleaner_map[str(cp["id"])] = f"{fn} {ln}".strip() or "Cleaner"
            except Exception:
                pass

        service_map = {}
        if service_ids:
            try:
                s_res = client.table("services").select("id,name").in_("id", service_ids).execute()
                for s in unwrap_response(s_res) or []:
                    service_map[str(s["id"])] = s.get("name", "Standard Clean")
            except Exception:
                pass

        for r in rows:
            cid = str(r.get("client_id") or "")
            clid = str(r.get("cleaner_id") or "")
            sid = str(r.get("service_type_id") or "")
            r["client_name"] = client_map.get(cid, f"Client {cid[:6]}" if cid else "-")
            r["cleaner_name"] = cleaner_map.get(clid, "Unassigned" if not clid else f"Cleaner {clid[:6]}")
            r["service_name"] = service_map.get(sid, "Cleaning")

        if search:
            s_lower = search.lower()
            rows = [
                r for r in rows
                if s_lower in str(r.get("id", "")).lower()
                or s_lower in r.get("client_name", "").lower()
                or s_lower in r.get("cleaner_name", "").lower()
                or s_lower in r.get("service_name", "").lower()
            ]

    except Exception:
        logger.exception("Unable to load bookings")
        error = "Booking records are currently unavailable."

    return templates.TemplateResponse(request, "bookings.html", {"request": request, "admin": admin, "active_page": "bookings", "bookings": rows, "error": error, "status_filter": status})


@router.get("/export")
def export_bookings(request: Request, status: str = ""):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "bookings:view")

    try:
        client = get_supabase_admin_client()
        query = client.table("bookings").select("*")
        if status:
            query = query.eq("status", status)
        response = query.order("scheduled_start", desc=True).execute()
        rows = unwrap_response(response) or []
    except Exception:
        rows = []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Booking ID", "Status", "Payment Method", "Gross Amount", "Currency", "Platform Fee", "Cleaner Net", "Scheduled Start", "Scheduled End", "Created At"])
    for b in rows:
        writer.writerow([
            b.get("id"),
            b.get("status"),
            b.get("payment_method"),
            b.get("gross_amount"),
            b.get("currency", "ZAR"),
            b.get("platform_fee", 0),
            b.get("cleaner_net_amount", 0),
            b.get("scheduled_start"),
            b.get("scheduled_end"),
            b.get("created_at"),
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bookings_export.csv"},
    )


@router.post("/{booking_id}/cancel")
def cancel_booking(
    request: Request,
    booking_id: str,
    reason: str = Form("Cancelled by administrator"),
    queue_refund: bool = Form(True),
):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "bookings:manage")

    client = get_supabase_admin_client()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        client.table("bookings").update({"status": "CANCELLED", "cancelled_at": now_iso, "updated_at": now_iso}).eq("id", booking_id).execute()
        AuditService(client).record(
            AdminActionCreate(
                admin_user_id=admin.user_id,
                action="BOOKING_CANCELLED",
                target_type="bookings",
                target_id=booking_id,
                reason=reason,
            )
        )

        # Check for paid payments on this booking and queue refund if applicable
        if queue_refund:
            try:
                pay_res = client.table("payments").select("id,payer_id,amount,status").eq("booking_id", booking_id).eq("status", "PAID").execute()
                paid_payments = unwrap_response(pay_res) or []
                for p in paid_payments:
                    # Check if already refunded
                    ref_res = client.table("refunds").select("id").eq("payment_id", p["id"]).execute()
                    if not (unwrap_response(ref_res) or []):
                        client.table("refunds").insert({
                            "payment_id": p["id"],
                            "booking_id": booking_id,
                            "requested_by": p.get("payer_id") or admin.user_id,
                            "amount": p["amount"],
                            "reason": f"Automatic refund request: Booking {booking_id[:8]} cancelled ({reason})",
                            "status": "REQUESTED",
                            "created_at": now_iso,
                            "updated_at": now_iso,
                        }).execute()
            except Exception:
                logger.exception("Failed to auto-queue refund on booking cancellation")

        # Record booking_cancellations row
        try:
            client.table("booking_cancellations").insert({
                "booking_id": booking_id,
                "cancelled_by": admin.user_id,
                "reason": reason,
                "refund_eligible": True,
                "refund_amount": 0,
            }).execute()
        except Exception:
            pass

    except Exception:
        logger.exception("Failed to cancel booking %s", booking_id)

    return RedirectResponse(url="/bookings", status_code=303)


@router.post("/{booking_id}/complete")
def complete_booking(request: Request, booking_id: str, reason: str = Form("Completed by administrator")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "bookings:manage")

    client = get_supabase_admin_client()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        client.table("bookings").update({"status": "COMPLETED", "completed_at": now_iso, "updated_at": now_iso}).eq("id", booking_id).execute()
        AuditService(client).record(
            AdminActionCreate(
                admin_user_id=admin.user_id,
                action="BOOKING_COMPLETED",
                target_type="bookings",
                target_id=booking_id,
                reason=reason,
            )
        )
    except Exception:
        logger.exception("Failed to complete booking %s", booking_id)

    return RedirectResponse(url="/bookings", status_code=303)
