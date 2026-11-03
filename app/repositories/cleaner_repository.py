import logging
import time
from typing import Any

from app.core.supabase import unwrap_response
from app.repositories.base import SupabaseRepository

logger = logging.getLogger(__name__)

_signed_url_cache: dict[tuple[str, str], tuple[float, str]] = {}


class CleanerRepository(SupabaseRepository):
    table_name = "cleaner_profiles"

    def get_signed_url(self, bucket: str, path: str, expires_in: int = 3600) -> str | None:
        if not path:
            return None
        now = time.time()
        cache_key = (bucket, path)
        if cache_key in _signed_url_cache:
            exp_time, cached_url = _signed_url_cache[cache_key]
            if now < exp_time:
                return cached_url
        try:
            storage_client = getattr(self.client, "storage", None)
            if not storage_client:
                return None
            res = storage_client.from_(bucket).create_signed_url(path, expires_in)
            url = None
            if isinstance(res, dict):
                url = res.get("signedURL") or res.get("signedUrl") or res.get("url")
            elif hasattr(res, "signed_url"):
                url = res.signed_url
            elif hasattr(res, "signedURL"):
                url = res.signedURL
            else:
                url = str(res)
            if url:
                _signed_url_cache[cache_key] = (now + expires_in - 60, url)
            return url
        except Exception as e:
            logger.warning(f"Could not create signed url for {bucket}/{path}: {e}")
            return None

    def list_cleaners(self, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_cleaner_profiles()
        response = (
            self.table()
            .select("id,user_id,verification_status,subscription_status,availability_status,overall_rating,completed_jobs_count,created_at,updated_at,profiles(first_name,last_name,phone,account_status)")
            .order("updated_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = unwrap_response(response) or []

        # 1. Cleaner IDs
        cleaner_ids = [row["id"] for row in rows if isinstance(row, dict) and "id" in row]

        # 2. Document types
        doc_types = []
        try:
            dt_res = self.client.table("verification_document_types").select("*").order("display_order", desc=False).execute()
            doc_types = unwrap_response(dt_res) or []
        except Exception as e:
            logger.warning(f"Could not load verification_document_types: {e}")
        doc_types_by_id = {dt["id"]: dt for dt in doc_types if isinstance(dt, dict) and "id" in dt}

        # 3. Fetch all cleaner verification documents
        docs_by_cleaner_id: dict[str, list[dict[str, Any]]] = {cid: [] for cid in cleaner_ids}
        if cleaner_ids:
            try:
                doc_res = (
                    self.client.table("cleaner_verification_documents")
                    .select("*")
                    .in_("cleaner_id", cleaner_ids)
                    .order("created_at", desc=True)
                    .execute()
                )
                raw_docs = unwrap_response(doc_res) or []
                for doc in raw_docs:
                    cid = doc.get("cleaner_id")
                    if cid in docs_by_cleaner_id:
                        dt_id = doc.get("document_type_id")
                        dt_info = doc_types_by_id.get(dt_id, {})
                        doc["document_type_name"] = dt_info.get("name") or "Verification Document"
                        doc["document_type_description"] = dt_info.get("description")
                        doc["document_type_required"] = dt_info.get("required", True)

                        doc_id = doc.get("id")
                        doc["download_url"] = f"/cleaners/{cid}/documents/{doc_id}/download"
                        doc["preview_url"] = doc["download_url"]

                        storage_path = doc.get("storage_path")
                        cache_key = ("cleaner-verification-documents", storage_path) if storage_path else None
                        if cache_key and cache_key in _signed_url_cache:
                            doc["signed_url"] = _signed_url_cache[cache_key][1]
                        else:
                            doc["signed_url"] = None

                        mime = (doc.get("mime_type") or "").lower()
                        doc["is_pdf"] = "pdf" in mime
                        doc["is_image"] = "image" in mime

                        sz = doc.get("file_size") or 0
                        if sz < 1024 * 1024:
                            doc["file_size_display"] = f"{round(sz / 1024, 1)} KB" if sz else "0 KB"
                        else:
                            doc["file_size_display"] = f"{round(sz / (1024 * 1024), 2)} MB"

                        docs_by_cleaner_id[cid].append(doc)
            except Exception as e:
                logger.warning(f"Error loading cleaner documents in list_cleaners: {e}")

        # 4. Attach diagnostics & documents to each cleaner row
        for row in rows:
            cid = row.get("id")
            try:
                diagnostic = self.client.rpc(
                    "cleaner_visibility_check",
                    {"p_cleaner_id": cid},
                ).execute()
                checks = unwrap_response(diagnostic) or []
            except Exception:
                checks = []
            row["visibility_checks"] = checks
            blocking = [check for check in checks if check.get("blocks_client_search")]
            row["visible_to_clients"] = bool(blocking) and all(check.get("passed") for check in blocking)

            cleaner_docs = docs_by_cleaner_id.get(cid, [])
            row["documents"] = cleaner_docs
            row["total_documents"] = len(cleaner_docs)
            row["has_documents"] = bool(cleaner_docs)

            # If cleaner has uploaded documents, their status must not be NOT_SUBMITTED
            if cleaner_docs and row.get("verification_status") in [None, "", "NOT_SUBMITTED"]:
                row["verification_status"] = "PENDING_REVIEW"
                try:
                    self.table().update({"verification_status": "PENDING_REVIEW"}).eq("id", cid).execute()
                except Exception:
                    pass

        return rows

    def get_cleaner_document(self, cleaner_id: str, document_id: str) -> dict[str, Any] | None:
        try:
            res = (
                self.client.table("cleaner_verification_documents")
                .select("*")
                .eq("cleaner_id", cleaner_id)
                .eq("id", document_id)
                .limit(1)
                .execute()
            )
            rows = unwrap_response(res) or []
            if rows:
                doc = rows[0]
                storage_path = doc.get("storage_path")
                doc["signed_url"] = self.get_signed_url("cleaner-verification-documents", storage_path) if storage_path else None
                return doc
            return None
        except Exception as e:
            logger.warning(f"Error fetching cleaner document {document_id}: {e}")
            return None

    def _ensure_cleaner_profiles(self) -> None:
        try:
            # 1. Sync from Auth table
            auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
            if auth_admin:
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
                    user_role = str(u_meta.get("role", "")).upper()
                    if user_role == "CLEANER":
                        full_parts = u_meta.get("fullName", "").split() if u_meta.get("fullName") else []
                        fname = u_meta.get("firstName") or u_meta.get("first_name") or (full_parts[0] if full_parts else "Cleaner")
                        lname = u_meta.get("lastName") or u_meta.get("last_name") or (" ".join(full_parts[1:]) if len(full_parts) > 1 else "User")
                        phone = u_meta.get("phone") or ""

                        p_res = self.client.table("profiles").select("id").eq("id", uid_str).execute()
                        p_rows = unwrap_response(p_res) or []
                        if not p_rows:
                            try:
                                self.client.table("profiles").insert({
                                    "id": uid_str,
                                    "role": "CLEANER",
                                    "first_name": str(fname),
                                    "last_name": str(lname),
                                    "phone": str(phone) if phone else None,
                                    "account_status": "ACTIVE",
                                }).execute()
                            except Exception:
                                pass

            # 2. Ensure cleaner_profiles table rows and verification_applications rows
            p_res = self.client.table("profiles").select("id").eq("role", "CLEANER").execute()
            p_rows = unwrap_response(p_res) or []
            if not p_rows:
                return
            cleaner_user_ids = [r["id"] for r in p_rows if isinstance(r, dict) and "id" in r]
            if not cleaner_user_ids:
                return
            cp_res = self.table().select("id,user_id,verification_status").in_("user_id", cleaner_user_ids).execute()
            cp_rows = unwrap_response(cp_res) or []
            existing_user_ids = {r["user_id"]: r for r in cp_rows if isinstance(r, dict) and "user_id" in r}

            for uid in cleaner_user_ids:
                if uid not in existing_user_ids:
                    try:
                        ins_res = self.table().insert({
                            "user_id": uid,
                            "verification_status": "PENDING_REVIEW",
                            "subscription_status": "PENDING",
                            "availability_status": "AVAILABLE",
                        }).execute()
                        ins_rows = unwrap_response(ins_res) or []
                        if ins_rows:
                            new_cid = ins_rows[0].get("id")
                            if new_cid:
                                try:
                                    self.client.table("verification_applications").insert({
                                        "cleaner_id": new_cid,
                                        "status": "PENDING_REVIEW",
                                    }).execute()
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

    def get_by_id(self, cleaner_id: str) -> dict[str, Any] | None:
        try:
            response = self.table().select("*").eq("id", cleaner_id).maybe_single().execute()
            data = unwrap_response(response)
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            return None

    def update_status(self, cleaner_id: str, **fields: Any) -> dict[str, Any] | None:
        from datetime import datetime, timezone
        if "updated_at" not in fields:
            fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            response = self.table().update(fields).eq("id", cleaner_id).execute()
            data = unwrap_response(response) or []
            return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        except Exception:
            core_fields = {k: v for k, v in fields.items() if k in ["verification_status", "subscription_status", "availability_status", "updated_at"]}
            response = self.table().update(core_fields).eq("id", cleaner_id).execute()
            data = unwrap_response(response) or []
            return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)