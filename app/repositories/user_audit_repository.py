from typing import Any
import logging

from app.core.supabase import unwrap_response

logger = logging.getLogger(__name__)


class UserAuditRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def search_users(self, search: str = "", role: str = "", status: str = "", page: int = 1, page_size: int = 50, user_ids: list[str] | None = None) -> dict[str, Any]:
        self._ensure_all_auth_profiles()
        start = max(0, (page - 1) * page_size)

        try:
            base_query = self.client.table("profiles").select("id,role,first_name,last_name,phone,account_status,created_at")
            if role:
                base_query = base_query.eq("role", role)
            if status:
                base_query = base_query.eq("account_status", status)

            rows: list[dict[str, Any]] = []
            if search or user_ids is not None:
                term = "".join(ch for ch in search if ch.isalnum() or ch in " _-").strip()
                collected_rows: dict[str, dict[str, Any]] = {}

                # 1. Matches by profile name or phone
                if term:
                    try:
                        name_q = self.client.table("profiles").select("id,role,first_name,last_name,phone,account_status,created_at")
                        if role:
                            name_q = name_q.eq("role", role)
                        if status:
                            name_q = name_q.eq("account_status", status)
                        name_q = name_q.or_(f"first_name.ilike.%{term}%,last_name.ilike.%{term}%,phone.ilike.%{term}%")
                        res = name_q.execute()
                        for r in unwrap_response(res) or []:
                            if isinstance(r, dict) and "id" in r:
                                collected_rows[r["id"]] = r
                    except Exception as n_exc:
                        logger.warning("Name/phone search query warning: %s", n_exc)

                # 2. Matches by auth email (user_ids)
                if user_ids:
                    try:
                        email_q = self.client.table("profiles").select("id,role,first_name,last_name,phone,account_status,created_at")
                        if role:
                            email_q = email_q.eq("role", role)
                        if status:
                            email_q = email_q.eq("account_status", status)
                        email_q = email_q.in_("id", user_ids)
                        res = email_q.execute()
                        for r in unwrap_response(res) or []:
                            if isinstance(r, dict) and "id" in r:
                                collected_rows[r["id"]] = r
                    except Exception as e_exc:
                        logger.warning("User IDs search query warning: %s", e_exc)

                all_found = list(collected_rows.values())
                all_found.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
                count = len(all_found)
                rows = all_found[start : start + page_size]
            else:
                resp = base_query.order("created_at", desc=True).limit(500).execute()
                all_found = unwrap_response(resp) or []
                count = len(all_found)
                rows = all_found[start : start + page_size]

        except Exception as exc:
            logger.warning("Primary profiles search query failed (%s); trying fallback.", exc)
            try:
                fb_query = self.client.table("profiles").select("id,role,first_name,last_name,phone,account_status,created_at")
                if role:
                    fb_query = fb_query.eq("role", role)
                if status:
                    fb_query = fb_query.eq("account_status", status)
                fb_resp = fb_query.order("created_at", desc=True).limit(page_size).execute()
                rows = unwrap_response(fb_resp) or []
                count = len(rows)
            except Exception as fb_exc:
                logger.error("Profiles fallback search also failed: %s", fb_exc)
                rows = []
                count = 0

        cleaner_ids = [r["id"] for r in rows if isinstance(r, dict) and r.get("role") == "CLEANER" and "id" in r]
        if cleaner_ids:
            try:
                cp_resp = self.client.table("cleaner_profiles").select("user_id,verification_status,overall_rating").in_("user_id", cleaner_ids).execute()
                cp_rows = unwrap_response(cp_resp) or []
                existing_uids = {item["user_id"] for item in cp_rows if isinstance(item, dict) and "user_id" in item}
                for uid in cleaner_ids:
                    if uid not in existing_uids:
                        try:
                            self.client.table("cleaner_profiles").insert({
                                "user_id": uid,
                                "verification_status": "PENDING_REVIEW",
                                "subscription_status": "PENDING",
                                "availability_status": "AVAILABLE",
                            }).execute()
                            cp_rows.append({"user_id": uid, "verification_status": "PENDING_REVIEW", "overall_rating": 0})
                        except Exception:
                            pass
                cp_map = {item["user_id"]: item for item in cp_rows if isinstance(item, dict) and "user_id" in item}
                for r in rows:
                    if isinstance(r, dict) and r.get("id") in cp_map:
                        r["cleaner_profiles"] = cp_map[r["id"]]
            except Exception:
                logger.warning("Could not enrich cleaner profile details for user search.")

        client_ids = [r["id"] for r in rows if isinstance(r, dict) and r.get("role") == "CLIENT" and "id" in r]
        if client_ids:
            try:
                civ_resp = self.client.table("client_identity_verifications").select("user_id,status,method,verified_at").in_("user_id", client_ids).execute()
                civ_rows = unwrap_response(civ_resp) or []
                civ_map = {item["user_id"]: item for item in civ_rows if isinstance(item, dict) and "user_id" in item}
                for r in rows:
                    if isinstance(r, dict) and r.get("id") in civ_map:
                        r["client_verification"] = civ_map[r["id"]]
            except Exception:
                logger.warning("Could not enrich client verification details for user search.")

        return {"rows": rows, "count": count}

    def _ensure_all_auth_profiles(self) -> None:
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

                raw_role = str(u_meta.get("role", "")).upper()
                if not raw_role and a_meta.get("admin_role"):
                    raw_role = "ADMIN"
                role = "ADMIN" if raw_role == "ADMIN" else "CLEANER" if raw_role == "CLEANER" else "CLIENT"

                full_parts = u_meta.get("fullName", "").split() if u_meta.get("fullName") else []
                fname = u_meta.get("firstName") or u_meta.get("first_name") or (full_parts[0] if full_parts else role.title())
                lname = u_meta.get("lastName") or u_meta.get("last_name") or (" ".join(full_parts[1:]) if len(full_parts) > 1 else "User")
                phone = u_meta.get("phone") or ""

                p_res = self.client.table("profiles").select("id").eq("id", uid_str).execute()
                p_rows = unwrap_response(p_res) or []
                if not p_rows:
                    try:
                        self.client.table("profiles").insert({
                            "id": uid_str,
                            "role": role,
                            "first_name": str(fname),
                            "last_name": str(lname),
                            "phone": str(phone) if phone else None,
                            "account_status": "ACTIVE",
                        }).execute()
                    except Exception:
                        pass

                # Also ensure role-specific child profiles exist so no user or data is left behind
                if role == "CLIENT":
                    try:
                        cl_res = self.client.table("client_profiles").select("id").eq("user_id", uid_str).execute()
                        if not unwrap_response(cl_res):
                            self.client.table("client_profiles").insert({"user_id": uid_str}).execute()
                    except Exception:
                        pass
                elif role == "CLEANER":
                    try:
                        cp_res = self.client.table("cleaner_profiles").select("id").eq("user_id", uid_str).execute()
                        if not unwrap_response(cp_res):
                            self.client.table("cleaner_profiles").insert({
                                "user_id": uid_str,
                                "verification_status": "PENDING_REVIEW",
                                "subscription_status": "PENDING",
                                "availability_status": "AVAILABLE",
                            }).execute()
                    except Exception:
                        pass
                elif role == "ADMIN":
                    try:
                        ap_res = self.client.table("admin_profiles").select("id").eq("user_id", uid_str).execute()
                        if not unwrap_response(ap_res):
                            adm_role = a_meta.get("admin_role") or "SUPPORT_AGENT"
                            self.client.table("admin_profiles").insert({
                                "user_id": uid_str,
                                "admin_role": adm_role,
                                "active": True,
                            }).execute()
                    except Exception:
                        pass
        except Exception:
            pass

    def list_audit_logs(
        self,
        event_type: str = "",
        actor_id: str = "",
        actor_ids: list[str] | None = None,
        target_type: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        audit_rows: list[dict[str, Any]] = []
        try:
            query = self.client.table("audit_logs").select("*")
            if event_type:
                query = query.ilike("action", f"%{event_type}%")
            if actor_id:
                query = query.eq("actor_id", actor_id)
            if target_type:
                query = query.eq("target_type", target_type)
            res = query.order("created_at", desc=True).limit(1000).execute()
            audit_rows = unwrap_response(res) or []
        except Exception as exc:
            logger.warning("Audit logs primary query warning: %s", exc)

        # Query admin_actions to ensure no admin action is left behind
        admin_rows: list[dict[str, Any]] = []
        try:
            fb_query = self.client.table("admin_actions").select("*")
            if event_type:
                fb_query = fb_query.ilike("action", f"%{event_type}%")
            if actor_id:
                fb_query = fb_query.eq("admin_user_id", actor_id)
            if target_type:
                fb_query = fb_query.eq("target_type", target_type)
            fb_res = fb_query.order("created_at", desc=True).limit(1000).execute()
            admin_rows = unwrap_response(fb_res) or []
        except Exception as fb_exc:
            logger.warning("Admin actions query warning: %s", fb_exc)

        # Deduplicate and merge: if an action is in admin_actions but missing from audit_logs, include it
        known_action_ids: set[str] = set()
        for r in audit_rows:
            ns = r.get("new_state")
            if isinstance(ns, dict) and ns.get("admin_action_id"):
                known_action_ids.add(str(ns["admin_action_id"]))
            if r.get("id"):
                known_action_ids.add(str(r["id"]))

        merged_rows = list(audit_rows)
        for aa in admin_rows:
            aa_id = str(aa.get("id") or "")
            if aa_id and aa_id in known_action_ids:
                continue
            merged_rows.append({
                "id": aa.get("id"),
                "actor_id": aa.get("admin_user_id"),
                "actor_role": "ADMIN",
                "action": aa.get("action"),
                "target_type": aa.get("target_type"),
                "target_id": aa.get("target_id"),
                "reason": aa.get("reason"),
                "metadata": aa.get("metadata", {}),
                "new_state": None,
                "previous_state": None,
                "ip_address": None,
                "created_at": aa.get("created_at"),
            })

        # Apply actor_ids filter if specific matching IDs were provided
        if actor_ids is not None:
            id_set = {str(i) for i in actor_ids}
            merged_rows = [
                r for r in merged_rows
                if str(r.get("actor_id") or r.get("admin_user_id") or "") in id_set
            ]

        # Apply target_type filter if needed in memory
        if target_type:
            merged_rows = [
                r for r in merged_rows
                if str(r.get("target_type") or "").lower() == target_type.lower()
            ]

        # Apply event_type filter if needed in memory
        if event_type:
            merged_rows = [
                r for r in merged_rows
                if event_type.lower() in str(r.get("action") or "").lower()
            ]

        # Sort descending by created_at
        merged_rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)

        count = len(merged_rows)
        start = max(0, (page - 1) * page_size)
        paginated_rows = merged_rows[start : start + page_size]

        return {"rows": paginated_rows, "count": count}