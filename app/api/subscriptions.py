import logging
from datetime import date

from fastapi import APIRouter, Form, Request
from starlette.responses import RedirectResponse

from app.core.supabase import get_supabase_admin_client
from app.core.templates import templates
from app.dependencies import current_admin, ensure_permission, redirect_to_login
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService

from app.services.cleaner_service import CleanerService

router = APIRouter(prefix="/subscriptions")
logger = logging.getLogger(__name__)


@router.get("")
def subscriptions(request: Request, prefill_cleaner: str = "", activation_error: str = "", error: str | None = None):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "subscriptions:view")

    rows = []
    period = None
    plans = []
    cleaners_list = []
    error = {
        "permission": (
            "Subscription activation was denied by Supabase. Restart the admin server after "
            "installing the login-client fix, and check its server-only Supabase secret key. "
            "Do not grant subscription writes to ordinary authenticated users. "
            "Check subscription history before retrying."
        ),
        "failed": (
            "Subscription activation could not be completed. Check subscription history "
            "before retrying: part of the operation may have been saved. "
            "See the admin server terminal for the error code."
        ),
    }.get(activation_error) or ("Subscription activation could not be completed. Check subscription history before retrying." if error else None)
    try:
        client = get_supabase_admin_client()
        sub_service = SubscriptionService(client)
        rows = SubscriptionRepository(client).list_recent()
        period = sub_service.calculate_period(date.today())
        plans = sub_service.list_plans()
        cleaners_list = CleanerService(client).list_cleaners()
    except Exception:
        logger.exception("Unable to load subscriptions")
        if not error:
            error = "Subscription records are unavailable."

    return templates.TemplateResponse(
        request,
        "subscriptions.html",
        {
            "request": request,
            "admin": admin,
            "active_page": "subscriptions",
            "subscriptions": rows,
            "period": period,
            "plans": plans,
            "cleaners": cleaners_list,
            "prefill_cleaner": prefill_cleaner,
            "today": date.today().isoformat(),
            "error": error,
        },
    )


@router.post("/activate")
def activate_subscription(request: Request, cleaner_id: str = Form(...), plan_id: str = Form(...), payment_id: str = Form(""), payment_date: date = Form(...), reason: str = Form("Administrative subscription activation")):
    admin = current_admin(request)
    if admin is None:
        return redirect_to_login()
    ensure_permission(admin, "subscriptions:manage")
    try:
        SubscriptionService(get_supabase_admin_client()).activate_subscription(admin.user_id, cleaner_id, plan_id, payment_id, payment_date, reason)
    except Exception as exc:
        code = getattr(exc, "code", None)
        logger.error("Subscription activation failed (database code: %s)", code or type(exc).__name__)
        category = "permission" if code == "42501" else "failed"
        return RedirectResponse(url=f"/subscriptions?activation_error={category}", status_code=303)
    return RedirectResponse(url="/subscriptions", status_code=303)