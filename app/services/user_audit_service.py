from math import ceil
from typing import Any
import logging
import uuid

from app.core.supabase import unwrap_response
from app.repositories.user_audit_repository import UserAuditRepository

logger = logging.getLogger(__name__)


class UserAuditService:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.repository = UserAuditRepository(client)

    def search_users(self, **filters: Any) -> dict[str, Any]:
        emails = self._auth_email_map()
        search = str(filters.get("search", "")).strip().lower()
        email_ids = [user_id for user_id, email in emails.items() if search and search in email.lower()]

        try:
            result = self.repository.search_users(**filters, user_ids=email_ids if email_ids else None)
        except Exception as exc:
            logger.warning("Error executing search_users query in repository: %s", exc)
            result = {"rows": [], "count": 0}

        rows = result.get("rows", [])
        for row in rows:
            if isinstance(row, dict):
                uid = str(row.get("id", ""))
                row["email"] = emails.get(uid, "")
                if row.get("role") == "CLEANER":
                    relation = row.get("cleaner_profiles") or {}
                    cleaner = relation[0] if isinstance(relation, list) and relation else relation if isinstance(relation, dict) else {}
                    row["verification_status"] = cleaner.get("verification_status")
                    row["rating"] = cleaner.get("overall_rating")
                elif row.get("role") == "CLIENT":
                    civ = row.get("client_verification") or {}
                    row["verification_status"] = (civ.get("status") or "UNVERIFIED").upper()
                    row["rating"] = None
                else:
                    row["verification_status"] = None
                    row["rating"] = None

        result["pages"] = max(1, ceil(result.get("count", len(rows)) / 50))
        return result

    def audit_logs(self, **filters: Any) -> dict[str, Any]:
        actor_query = str(filters.get("actor_id", "")).strip()
        matched_actor_ids: list[str] | None = None

        if actor_query:
            try:
                uuid.UUID(actor_query)
                matched_actor_ids = [actor_query]
            except ValueError:
                matched_actor_ids = self._resolve_actor_query(actor_query)

        repo_filters = dict(filters)
        if matched_actor_ids is not None:
            repo_filters["actor_id"] = ""
            repo_filters["actor_ids"] = matched_actor_ids

        try:
            result = self.repository.list_audit_logs(**repo_filters)
        except Exception as exc:
            logger.warning("Error loading audit logs from repository: %s", exc)
            result = {"rows": [], "count": 0}

        rows = result.get("rows", [])
        self._enrich_audit_rows(rows)

        page_size = int(filters.get("page_size", 50))
        result["pages"] = max(1, ceil(result.get("count", len(rows)) / page_size))
        return result

    def _resolve_actor_query(self, query: str) -> list[str]:
        term = query.lower()
        matched_ids: set[str] = set()

        auth_users = self._auth_user_map()
        for uid, udata in auth_users.items():
            if term in udata.get("email", "").lower():
                matched_ids.add(uid)
            meta = udata.get("metadata", {})
            full_n = f"{meta.get('fullName', '')} {meta.get('firstName', '')} {meta.get('lastName', '')}".lower()
            if term in full_n:
                matched_ids.add(uid)

        try:
            p_res = self.client.table("profiles").select("id,first_name,last_name").execute()
            for p in unwrap_response(p_res) or []:
                pid = str(p.get("id"))
                full_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".lower()
                if term in full_name:
                    matched_ids.add(pid)
        except Exception:
            pass

        return list(matched_ids)

    def _enrich_audit_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return

        try:
            p_res = self.client.table("profiles").select("id,first_name,last_name,role,phone").execute()
            profiles_map = {p["id"]: p for p in (unwrap_response(p_res) or []) if isinstance(p, dict) and "id" in p}
        except Exception:
            profiles_map = {}

        try:
            ap_res = self.client.table("admin_profiles").select("user_id,admin_role").execute()
            admin_roles = {ap["user_id"]: ap.get("admin_role") for ap in (unwrap_response(ap_res) or []) if isinstance(ap, dict) and "user_id" in ap}
        except Exception:
            admin_roles = {}

        try:
            cp_res = self.client.table("cleaner_profiles").select("id,user_id").execute()
            cleaner_map = {cp["id"]: cp.get("user_id") for cp in (unwrap_response(cp_res) or []) if isinstance(cp, dict) and "id" in cp}
        except Exception:
            cleaner_map = {}

        try:
            clp_res = self.client.table("client_profiles").select("id,user_id").execute()
            client_map = {cp["id"]: cp.get("user_id") for cp in (unwrap_response(clp_res) or []) if isinstance(cp, dict) and "id" in cp}
        except Exception:
            client_map = {}

        auth_users = self._auth_user_map()

        for row in rows:
            if not isinstance(row, dict):
                continue

            actor_id = str(row.get("actor_id") or row.get("admin_user_id") or "")
            actor_prof = profiles_map.get(actor_id)
            auth_user = auth_users.get(actor_id) or {}
            role_label = admin_roles.get(actor_id) or row.get("actor_role") or (actor_prof.get("role") if actor_prof else "ADMIN")

            actor_name = ""
            if actor_prof:
                first = actor_prof.get("first_name", "") or ""
                last = actor_prof.get("last_name", "") or ""
                actor_name = f"{first} {last}".strip()

            if not actor_name and auth_user:
                meta = auth_user.get("metadata", {}) or {}
                actor_name = meta.get("fullName") or meta.get("full_name") or f"{meta.get('firstName', '')} {meta.get('lastName', '')}".strip()

            if not actor_name:
                if actor_id:
                    actor_name = f"Admin ({actor_id[:8]})"
                else:
                    actor_name = "System Administrator"

            actor_email = auth_user.get("email", "")

            # Target Resolution
            target_type = str(row.get("target_type") or "").lower()
            target_id = str(row.get("target_id") or "")
            target_name = None

            if target_type == "profiles":
                tp = profiles_map.get(target_id)
                if tp:
                    target_name = f"{tp.get('first_name', '')} {tp.get('last_name', '')}".strip()
            elif target_type == "cleaner_profiles":
                uid = cleaner_map.get(target_id) or target_id
                tp = profiles_map.get(uid)
                if tp:
                    target_name = f"{tp.get('first_name', '')} {tp.get('last_name', '')}".strip()
            elif target_type == "client_profiles":
                uid = client_map.get(target_id) or target_id
                tp = profiles_map.get(uid)
                if tp:
                    target_name = f"{tp.get('first_name', '')} {tp.get('last_name', '')}".strip()

            # Readable reason
            reason = row.get("reason")
            if not reason and isinstance(row.get("metadata"), dict):
                reason = row.get("metadata", {}).get("reason")
            if not reason and isinstance(row.get("new_state"), dict):
                ns = row.get("new_state", {})
                ns_meta = ns.get("metadata")
                if isinstance(ns_meta, dict):
                    reason = ns_meta.get("reason")
                if not reason and "reason" in ns:
                    reason = ns.get("reason")

            row["actor_name"] = actor_name
            row["actor_email"] = actor_email
            row["actor_role_label"] = role_label
            row["target_name"] = target_name
            row["display_reason"] = reason or "-"

    def _auth_user_map(self) -> dict[str, dict[str, Any]]:
        users_map: dict[str, dict[str, Any]] = {}
        try:
            admin_api = getattr(getattr(self.client, "auth", None), "admin", None)
            if admin_api and hasattr(admin_api, "list_users"):
                page = 1
                while True:
                    response = admin_api.list_users(page=page, per_page=1000)
                    users = []
                    if isinstance(response, list):
                        users = response
                    elif hasattr(response, "users") and isinstance(response.users, list):
                        users = response.users
                    elif hasattr(response, "data") and isinstance(response.data, list):
                        users = response.data
                    else:
                        try:
                            users = list(response)
                        except Exception:
                            users = []

                    for user in users:
                        uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
                        uemail = getattr(user, "email", None) or (user.get("email") if isinstance(user, dict) else None)
                        umeta = getattr(user, "user_metadata", None) or (user.get("user_metadata") if isinstance(user, dict) else {})
                        if uid:
                            users_map[str(uid)] = {
                                "email": str(uemail) if uemail else "",
                                "metadata": umeta if isinstance(umeta, dict) else {},
                            }

                    if not users or len(users) < 1000:
                        break
                    page += 1
        except Exception as exc:
            logger.warning("Unable to fetch Supabase Auth user list: %s", exc)
        return users_map

    def _auth_email_map(self) -> dict[str, str]:
        return {uid: data.get("email", "") for uid, data in self._auth_user_map().items()}