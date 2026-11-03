import logging
import os
from fastapi import APIRouter, Form, Request
from starlette.responses import RedirectResponse

from app.config import get_settings
from app.core.supabase import get_supabase_admin_client, get_supabase_auth_client
from app.core.templates import templates
from app.exceptions import AuthenticationRateLimitError, AuthenticationUnavailableError, CleanLinkAdminError, PermissionDeniedError
from app.services.auth_service import AuthService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/__check_env__")
def check_env():
    settings = get_settings()
    key = settings.supabase_service_role_key or ""
    all_keys = [k for k in os.environ.keys() if "SUPABASE" in k.upper() or "SECRET" in k.upper() or "KEY" in k.upper() or "ADMIN" in k.upper()]
    return {
        "supabase_url": settings.supabase_url,
        "has_key": bool(key),
        "key_length": len(key),
        "discovered_env_var_names": all_keys,
        "has_session_secret": bool(settings.session_secret),
    }


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        session = AuthService(get_supabase_admin_client(), get_supabase_auth_client()).sign_in(email, password)
    except CleanLinkAdminError as exc:
        logger.info("Admin login rejected for %s: %s", email, exc)
        status_code = 401
        if isinstance(exc, AuthenticationUnavailableError):
            status_code = 503
        elif isinstance(exc, AuthenticationRateLimitError):
            status_code = 429
        elif isinstance(exc, PermissionDeniedError):
            status_code = 403
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": str(exc)}, status_code=status_code)
    except Exception as e:
        logger.exception("Unexpected admin login failure")
        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request, "error": f"Admin login is temporarily unavailable: {type(e).__name__} - {e}"},
            status_code=503,
        )

    request.session["admin"] = session.model_dump(mode="json")
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
