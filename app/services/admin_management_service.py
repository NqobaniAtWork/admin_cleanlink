import logging
from typing import Any

from app.core.supabase import unwrap_response
from app.exceptions import AdminManagementError
from app.models.enums import AdminRole
from app.repositories.admin_repository import AdminRepository
from app.schemas.admin_actions import AdminActionCreate
from app.schemas.auth_schema import AdminCreate
from app.security.roles import (
    ALL_OPERATIONAL_PERMISSIONS,
    ROLE_PERMISSIONS,
    get_effective_permissions,
)
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class AdminManagementService:
    """Creates Auth users and manages database-backed administrator roles and granular permissions."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.admins = AdminRepository(client)
        self.audit = AuditService(client)

    def list_admins(self) -> list[dict[str, Any]]:
        rows = self.admins.list_admins()
        from app.services.user_audit_service import UserAuditService
        email_map = UserAuditService(self.client)._auth_email_map()

        auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
        auth_user_map: dict[str, Any] = {}
        if auth_admin:
            try:
                users_resp = auth_admin.list_users(page=1, per_page=1000)
                u_list = users_resp if isinstance(users_resp, list) else getattr(users_resp, "users", [])
                for u in u_list:
                    uid = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
                    if uid:
                        auth_user_map[str(uid)] = u
            except Exception:
                pass

        for r in rows:
            uid = str(r.get("user_id", ""))
            r["email"] = email_map.get(uid, "")
            perms = r.get("custom_permissions")
            if perms is None and uid in auth_user_map:
                u = auth_user_map[uid]
                a_meta = getattr(u, "app_metadata", {}) or (u.get("app_metadata") if isinstance(u, dict) else {}) or {}
                perms = a_meta.get("permissions")
                if perms is not None:
                    r["custom_permissions"] = list(perms)

            r["effective_permissions"] = list(
                get_effective_permissions(r.get("admin_role", "SUPPORT_ADMIN"), r.get("custom_permissions"))
            )
        return rows

    def get_admin_profile(self, user_id: str) -> dict[str, Any] | None:
        return self.admins.get_active_admin_by_user_id(user_id)

    def update_admin_permissions(
        self,
        actor_user_id: str,
        admin_id: str,
        permissions: list[str],
        role: str | None = None,
    ) -> dict[str, Any]:
        """Allows Super Admin to update or revoke permissions for any administrator."""
        ap_res = self.client.table("admin_profiles").select("id,user_id,admin_role").eq("id", admin_id).execute()
        ap_rows = unwrap_response(ap_res) or []
        if not ap_rows:
            raise AdminManagementError("Administrator profile not found.")
        target_admin = ap_rows[0]
        target_user_id = str(target_admin["user_id"])
        target_role = target_admin.get("admin_role")

        clean_permissions = [p for p in permissions if p != "admins:manage"]

        # Prevent accidental lockout if super admin edits their own account
        if target_user_id == actor_user_id and target_role == AdminRole.SUPER_ADMIN.value:
            if role and role != AdminRole.SUPER_ADMIN.value:
                raise AdminManagementError("You cannot demote your own Super Admin role.")

        res = self.admins.update_admin_permissions(admin_id, clean_permissions, role)

        # Sync to Supabase Auth app_metadata
        try:
            auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
            if auth_admin:
                u = auth_admin.get_user_by_id(target_user_id)
                current_app_meta = dict(getattr(u, "app_metadata", {}) or {})
                current_app_meta["permissions"] = clean_permissions
                if role:
                    current_app_meta["admin_role"] = role
                auth_admin.update_user_by_id(target_user_id, {"app_metadata": current_app_meta})
        except Exception as exc:
            logger.warning("Could not sync app_metadata permissions for user %s: %s", target_user_id, exc)

        # Record audit log
        self.audit.record(
            AdminActionCreate(
                admin_user_id=actor_user_id,
                action="ADMIN_PERMISSIONS_UPDATED",
                target_type="admin_profiles",
                target_id=admin_id,
                reason="Super admin updated administrator privileges",
                metadata={"permissions": clean_permissions, "role": role or target_role, "target_user_id": target_user_id},
            )
        )
        return res or target_admin

    def update_personal_details(self, user_id: str, first_name: str, last_name: str, phone: str = "", new_password: str = "") -> dict[str, Any] | None:
        first_name = first_name.strip()
        last_name = last_name.strip()
        if not first_name or not last_name:
            raise AdminManagementError("First and last name are required.")

        res = self.admins.update_personal_details(user_id, first_name, last_name, phone)

        try:
            auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
            if auth_admin:
                update_payload: dict[str, Any] = {
                    "user_metadata": {
                        "firstName": first_name,
                        "lastName": last_name,
                        "phone": phone.strip() if phone else None,
                    }
                }
                if new_password and len(new_password) >= 12:
                    update_payload["password"] = new_password
                elif new_password and len(new_password) < 12:
                    raise AdminManagementError("New password must be at least 12 characters.")

                auth_admin.update_user_by_id(user_id, update_payload)
        except AdminManagementError:
            raise
        except Exception:
            pass

        self.audit.record(
            AdminActionCreate(
                admin_user_id=user_id,
                action="PROFILE_UPDATED",
                target_type="profiles",
                target_id=user_id,
                reason="Administrator updated personal details",
                metadata={"first_name": first_name, "last_name": last_name, "phone": phone},
            )
        )
        return res

    def toggle_admin_status(self, actor_user_id: str, admin_id: str, active: bool) -> dict[str, Any] | None:
        res = self.admins.toggle_admin_status(admin_id, active)
        self.audit.record(
            AdminActionCreate(
                admin_user_id=actor_user_id,
                action="ADMIN_STATUS_TOGGLED",
                target_type="admin_profiles",
                target_id=admin_id,
                reason=f"Administrator marked {'active' if active else 'inactive'}",
                metadata={"active": active},
            )
        )
        return res

    def create_admin(self, actor_user_id: str, payload: AdminCreate) -> dict[str, Any]:
        return _create_admin(self.client, payload, actor_user_id, self.audit)


def _create_admin(client: Any, payload: AdminCreate, actor_user_id: str | None = None, audit: AuditService | None = None) -> dict[str, Any]:
    first_name = payload.first_name.strip()
    last_name = payload.last_name.strip()
    if not first_name or not last_name:
        raise AdminManagementError("First and last name are required.")
    if len(payload.password) < 12:
        raise AdminManagementError("Password must contain at least 12 characters.")

    role_val = payload.role.value if isinstance(payload.role, AdminRole) else str(payload.role)
    permissions = [p for p in (payload.permissions or []) if p != "admins:manage"]
    if role_val == AdminRole.SUPER_ADMIN.value:
        permissions = list(ALL_OPERATIONAL_PERMISSIONS)
    elif not permissions:
        role_enum = AdminRole(role_val) if role_val in AdminRole._value2member_map_ else None
        if role_enum:
            permissions = list(ROLE_PERMISSIONS.get(role_enum, set()))

    auth_user_id: str | None = None
    try:
        response = client.auth.admin.create_user({
            "email": str(payload.email), "password": payload.password, "email_confirm": True,
            "user_metadata": {"role": "ADMIN", "firstName": first_name, "lastName": last_name},
            "app_metadata": {"admin_role": role_val, "permissions": permissions},
        })
        user = getattr(response, "user", None)
        auth_user_id = str(getattr(user, "id", "") or "")
        if not auth_user_id:
            raise AdminManagementError("Supabase did not return the new administrator account.")
        client.table("profiles").insert({
            "id": auth_user_id, "role": "ADMIN", "first_name": first_name,
            "last_name": last_name, "account_status": "ACTIVE",
        }).execute()
        admin_profile = AdminRepository(client).create_admin_profile(auth_user_id, role_val, custom_permissions=permissions)
        if actor_user_id and audit:
            audit.record(AdminActionCreate(
                admin_user_id=actor_user_id, action="ADMIN_CREATED", target_type="admin_profile",
                target_id=admin_profile.get("id") or auth_user_id,
                metadata={"role": role_val, "email": str(payload.email), "permissions": permissions},
            ))
        return admin_profile
    except AdminManagementError:
        if auth_user_id:
            client.auth.admin.delete_user(auth_user_id)
        raise
    except Exception as exc:
        if auth_user_id:
            try:
                client.auth.admin.delete_user(auth_user_id)
            except Exception:
                pass
        raise AdminManagementError("Unable to create this administrator. The email may already be registered.") from exc


def reset_admin_password(client: Any, email: str, password: str) -> None:
    """Recover an existing administrator from a trusted, server-side terminal."""
    if len(password) < 12 or not password.strip():
        raise AdminManagementError("Choose a password containing at least 12 characters.")

    try:
        user = _find_auth_user_by_email(client, email)
        if not user or not getattr(user, "email_confirmed_at", None):
            raise AdminManagementError("No confirmed administrator was found. No password was changed.")
        user_id = str(user.id)
        profiles = client.table("profiles").select("role,account_status").eq("id", user_id).limit(1).execute().data or []
        admins = client.table("admin_profiles").select("admin_role,active").eq("user_id", user_id).limit(1).execute().data or []
        profile = profiles[0] if profiles else {}
        admin = admins[0] if admins else {}
        if (
            profile.get("role") != "ADMIN"
            or profile.get("account_status") != "ACTIVE"
            or admin.get("active") is not True
            or admin.get("admin_role") not in {role.value for role in AdminRole}
        ):
            raise AdminManagementError("This is not an active administrator. No password was changed.")
    except AdminManagementError:
        raise
    except Exception as exc:
        raise AdminManagementError("Administrator access could not be checked. No password was changed.") from exc

    try:
        response = client.auth.admin.update_user_by_id(user_id, {"password": password})
        if str(getattr(getattr(response, "user", None), "id", "")) != user_id:
            raise RuntimeError("Missing password-update confirmation")
    except Exception as exc:
        raise AdminManagementError(
            "The password update could not be confirmed. No account roles or permissions were changed."
        ) from exc


def bootstrap_super_admin(client: Any, payload: AdminCreate) -> dict[str, Any]:
    existing = AdminRepository(client).list_admins()
    if any(row.get("active") and row.get("admin_role") == AdminRole.SUPER_ADMIN.value for row in existing):
        raise AdminManagementError("An active super admin already exists. Add administrators from the admin portal.")
    if payload.role is not AdminRole.SUPER_ADMIN:
        raise AdminManagementError("The bootstrap command can only create a super admin.")

    existing_user = _find_auth_user_by_email(client, str(payload.email))
    if not existing_user:
        return _create_admin(client, payload)

    user_id = str(existing_user.id)
    profile_rows = client.table("profiles").select("id,role").eq("id", user_id).execute().data or []
    if profile_rows and profile_rows[0].get("role") != "ADMIN":
        raise AdminManagementError(
            "This email already belongs to a client or cleaner. Use a separate administrator email."
        )

    first_name = payload.first_name.strip()
    last_name = payload.last_name.strip()
    if not first_name or not last_name:
        raise AdminManagementError("First and last name are required.")
    if len(payload.password) < 12:
        raise AdminManagementError("Password must contain at least 12 characters.")

    all_permissions = list(ALL_OPERATIONAL_PERMISSIONS)
    user_metadata = dict(getattr(existing_user, "user_metadata", None) or {})
    app_metadata = dict(getattr(existing_user, "app_metadata", None) or {})
    user_metadata.update({"role": "ADMIN", "firstName": first_name, "lastName": last_name})
    app_metadata["admin_role"] = AdminRole.SUPER_ADMIN.value
    app_metadata["permissions"] = all_permissions
    client.auth.admin.update_user_by_id(
        user_id,
        {
            "password": payload.password,
            "email_confirm": True,
            "user_metadata": user_metadata,
            "app_metadata": app_metadata,
        },
    )

    if profile_rows:
        client.table("profiles").update({
            "first_name": first_name,
            "last_name": last_name,
            "account_status": "ACTIVE",
        }).eq("id", user_id).execute()
    else:
        client.table("profiles").insert({
            "id": user_id,
            "role": "ADMIN",
            "first_name": first_name,
            "last_name": last_name,
            "account_status": "ACTIVE",
        }).execute()

    admin_rows = client.table("admin_profiles").select("id").eq("user_id", user_id).execute().data or []
    if admin_rows:
        try:
            response = client.table("admin_profiles").update({
                "admin_role": AdminRole.SUPER_ADMIN.value,
                "active": True,
                "custom_permissions": all_permissions,
            }).eq("user_id", user_id).execute()
            return response.data[0]
        except Exception:
            response = client.table("admin_profiles").update({
                "admin_role": AdminRole.SUPER_ADMIN.value,
                "active": True,
            }).eq("user_id", user_id).execute()
            return response.data[0]
    return AdminRepository(client).create_admin_profile(user_id, AdminRole.SUPER_ADMIN.value, custom_permissions=all_permissions)


def _find_auth_user_by_email(client: Any, email: str) -> Any | None:
    normalized_email = email.strip().lower()
    page = 1
    while True:
        users = list(client.auth.admin.list_users(page=page, per_page=1000))
        matches = [user for user in users if (getattr(user, "email", "") or "").lower() == normalized_email]
        if len(matches) > 1:
            raise AdminManagementError("Multiple Auth users have this email; resolve the duplicate before bootstrapping.")
        if matches:
            return matches[0]
        if len(users) < 1000:
            return None
        page += 1
