from datetime import datetime, timezone
from typing import Any

from app.core.supabase import unwrap_response
from app.repositories.base import SupabaseRepository


class AdminRepository(SupabaseRepository):
    table_name = "admin_profiles"

    def get_active_admin_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        try:
            query = self.table().select("id,user_id,admin_role,active,custom_permissions,profiles(first_name,last_name,account_status)").eq("user_id", user_id).eq("active", True)
            if hasattr(query, "maybe_single"):
                response = query.maybe_single().execute()
            else:
                response = query.limit(1).execute()
            data = unwrap_response(response)
            if isinstance(data, dict):
                return data
            if isinstance(data, list) and data:
                return data[0]
        except Exception:
            pass

        try:
            query = self.table().select("id,user_id,admin_role,active,profiles(first_name,last_name,account_status)").eq("user_id", user_id).eq("active", True)
            if hasattr(query, "maybe_single"):
                response = query.maybe_single().execute()
            else:
                response = query.limit(1).execute()
            data = unwrap_response(response)
            if isinstance(data, dict):
                return data
            if isinstance(data, list) and data:
                return data[0]
        except Exception:
            pass

        try:
            res = self.table().select("id,user_id,admin_role,active").eq("user_id", user_id).eq("active", True).execute()
            rows = unwrap_response(res) or []
            if rows:
                admin_rec = dict(rows[0])
                try:
                    p_res = self.client.table("profiles").select("first_name,last_name,account_status").eq("id", user_id).execute()
                    p_rows = unwrap_response(p_res) or []
                    if p_rows:
                        admin_rec["profiles"] = p_rows[0]
                except Exception:
                    pass
                return admin_rec
        except Exception:
            pass

        return None

    def list_admins(self) -> list[dict[str, Any]]:
        self._ensure_auth_admins()
        try:
            response = (
                self.table()
                .select("id,user_id,admin_role,active,custom_permissions,created_at,profiles(first_name,last_name,phone,account_status)")
                .order("created_at", desc=False)
                .execute()
            )
            rows = unwrap_response(response) or []
            return rows
        except Exception:
            pass

        try:
            response = (
                self.table()
                .select("id,user_id,admin_role,active,created_at,profiles(first_name,last_name,phone,account_status)")
                .order("created_at", desc=False)
                .execute()
            )
            rows = unwrap_response(response) or []
            return rows
        except Exception:
            try:
                res = self.table().select("id,user_id,admin_role,active,created_at").order("created_at", desc=False).execute()
                rows = unwrap_response(res) or []
                for r in rows:
                    uid = r.get("user_id")
                    if uid:
                        p_res = self.client.table("profiles").select("first_name,last_name,phone,account_status").eq("id", uid).execute()
                        p_data = unwrap_response(p_res) or []
                        if p_data:
                            r["profiles"] = p_data[0]
                return rows
            except Exception:
                return []

    def _ensure_auth_admins(self) -> None:
        try:
            auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
            if not auth_admin:
                return
            users_resp = auth_admin.list_users(page=1, per_page=1000)
            users_list = users_resp if isinstance(users_resp, list) else getattr(users_resp, "users", [])
            for u in users_list:
                uid = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
                if not uid:
                    continue
                uid_str = str(uid)
                u_meta = getattr(u, "user_metadata", {}) or {}
                if not isinstance(u_meta, dict):
                    u_meta = {}
                a_meta = getattr(u, "app_metadata", {}) or {}
                if not isinstance(a_meta, dict):
                    a_meta = {}

                user_role = str(u_meta.get("role", "")).upper()
                admin_role = str(a_meta.get("admin_role", "") or u_meta.get("admin_role", "") or "SUPER_ADMIN").upper()
                perms = a_meta.get("permissions")
                if user_role == "ADMIN" or a_meta.get("admin_role"):
                    first_name = u_meta.get("firstName") or u_meta.get("first_name") or "Admin"
                    last_name = u_meta.get("lastName") or u_meta.get("last_name") or "User"
                    phone = u_meta.get("phone") or ""

                    p_res = self.client.table("profiles").select("id").eq("id", uid_str).execute()
                    p_rows = unwrap_response(p_res) or []
                    if not p_rows:
                        try:
                            self.client.table("profiles").insert({
                                "id": uid_str,
                                "role": "ADMIN",
                                "first_name": first_name,
                                "last_name": last_name,
                                "phone": phone if phone else None,
                                "account_status": "ACTIVE",
                            }).execute()
                        except Exception:
                            pass

                    ap_res = self.table().select("id").eq("user_id", uid_str).execute()
                    ap_rows = unwrap_response(ap_res) or []
                    if not ap_rows:
                        insert_data: dict[str, Any] = {
                            "user_id": uid_str,
                            "admin_role": admin_role if admin_role in ("SUPER_ADMIN", "VERIFICATION_ADMIN", "FINANCE_ADMIN", "SUPPORT_ADMIN") else "SUPER_ADMIN",
                            "active": True,
                        }
                        if perms is not None:
                            insert_data["custom_permissions"] = perms
                        try:
                            self.table().insert(insert_data).execute()
                        except Exception:
                            insert_data.pop("custom_permissions", None)
                            try:
                                self.table().insert(insert_data).execute()
                            except Exception:
                                pass
        except Exception:
            pass

    def create_admin_profile(
        self,
        user_id: str,
        admin_role: str,
        custom_permissions: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"user_id": user_id, "admin_role": admin_role, "active": True}
        if custom_permissions is not None:
            payload["custom_permissions"] = custom_permissions
        try:
            response = self.table().insert(payload).execute()
            rows = unwrap_response(response) or []
            return rows[0] if rows else {}
        except Exception:
            # Fallback if custom_permissions column does not exist yet
            payload.pop("custom_permissions", None)
            response = self.table().insert(payload).execute()
            rows = unwrap_response(response) or []
            return rows[0] if rows else {}

    def update_admin_permissions(
        self,
        admin_id: str,
        permissions: list[str],
        role: str | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "custom_permissions": permissions,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if role:
            payload["admin_role"] = role
        try:
            response = self.table().update(payload).eq("id", admin_id).execute()
            data = unwrap_response(response) or []
            return data[0] if data else None
        except Exception:
            # If custom_permissions column not in DB, update role only if provided
            if role:
                try:
                    res = self.table().update({"admin_role": role, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", admin_id).execute()
                    data = unwrap_response(res) or []
                    return data[0] if data else None
                except Exception:
                    pass
            return None

    def update_personal_details(self, user_id: str, first_name: str, last_name: str, phone: str = "") -> dict[str, Any] | None:
        payload = {
            "first_name": first_name.strip(),
            "last_name": last_name.strip(),
            "phone": phone.strip() if phone else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        response = self.client.table("profiles").update(payload).eq("id", user_id).execute()
        data = unwrap_response(response) or []
        return data[0] if data else None

    def toggle_admin_status(self, admin_id: str, active: bool) -> dict[str, Any] | None:
        response = self.table().update({"active": active, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", admin_id).execute()
        data = unwrap_response(response) or []
        return data[0] if data else None
