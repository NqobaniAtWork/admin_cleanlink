import logging
from typing import Any

from app.exceptions import (
    AuthenticationError,
    AuthenticationRateLimitError,
    AuthenticationUnavailableError,
    PermissionDeniedError,
)
from app.models.enums import AdminRole
from app.repositories.admin_repository import AdminRepository
from app.schemas.auth_schema import AdminSession

logger = logging.getLogger(__name__)


class AuthService:
    """Admin authentication backed by Supabase Auth plus admin_profiles."""

    def __init__(self, client: Any, auth_client: Any) -> None:
        if client is auth_client:
            raise ValueError("Admin database and login clients must be separate instances.")
        self.client = client
        self.auth_client = auth_client
        self.admins = AdminRepository(client)

    def sign_in(self, email: str, password: str) -> AdminSession:
        email = email.strip()
        try:
            response = self.auth_client.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as exc:
            code = getattr(exc, "code", None)
            status = getattr(exc, "status", None)
            # Older Auth responses omit the code; do not mistake an invalid API key for bad credentials.
            legacy_credentials = not code and status in (400, 401) and str(exc).lower() in (
                "invalid login credentials", "invalid email or password."
            )
            if code == "invalid_credentials" or legacy_credentials:
                raise AuthenticationError("Invalid email or password.") from exc
            if code == "email_not_confirmed":
                raise AuthenticationError("Confirm your email address before signing in.") from exc
            if code == "user_banned":
                raise PermissionDeniedError("This account cannot sign in. Contact a CleanLink administrator.") from exc
            if status == 429 or code == "over_request_rate_limit":
                raise AuthenticationRateLimitError("Too many sign-in attempts. Please wait before trying again.") from exc
            logger.warning("Admin authentication unavailable: type=%s code=%s status=%s", type(exc).__name__, code, status)
            raise AuthenticationUnavailableError("Admin sign-in is temporarily unavailable. Please try again shortly.") from exc

        user = getattr(response, "user", None)
        if user is None or getattr(user, "id", None) is None:
            raise AuthenticationUnavailableError("Admin sign-in is temporarily unavailable. Please try again shortly.")

        admin_profile = self.admins.get_active_admin_by_user_id(user.id)
        if not admin_profile or (admin_profile.get("profiles") or {}).get("account_status") != "ACTIVE":
            try:
                self.auth_client.auth.sign_out()
            finally:
                raise PermissionDeniedError("This account is not an active CleanLink administrator.")

        profile = admin_profile.get("profiles") or {}
        display_name = " ".join(part for part in [profile.get("first_name"), profile.get("last_name")] if part).strip() or "Admin"
        admin_prof_id = str(admin_profile["id"]) if admin_profile.get("id") else None

        # Check permissions in database admin_profile or fallback to user app_metadata
        custom_perms = admin_profile.get("custom_permissions")
        if custom_perms is None:
            app_meta = getattr(user, "app_metadata", {}) or {}
            custom_perms = app_meta.get("permissions")

        session = AdminSession(
            user_id=user.id,
            admin_profile_id=admin_prof_id,
            email=getattr(user, "email", email) or email,
            role=AdminRole(admin_profile["admin_role"]),
            display_name=display_name,
            custom_permissions=list(custom_perms or []),
        )
        try:
            from app.schemas.admin_actions import AdminActionCreate
            from app.services.audit_service import AuditService
            AuditService(self.client).record(
                AdminActionCreate(
                    admin_user_id=user.id,
                    action="ADMIN_LOGIN",
                    target_type="admin_sessions",
                    target_id=user.id,
                    reason=f"Admin signed in ({session.role.value})",
                    metadata={"email": session.email},
                )
            )
        except Exception:
            pass
        return session

    def sign_out(self) -> None:
        self.auth_client.auth.sign_out()
