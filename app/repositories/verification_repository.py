import logging
import time
from typing import Any

from app.core.supabase import unwrap_response
from app.repositories.base import SupabaseRepository

logger = logging.getLogger(__name__)

_signed_url_cache: dict[tuple[str, str], tuple[float, str]] = {}


class VerificationRepository(SupabaseRepository):
    table_name = "verification_applications"

    def list_document_types(self) -> list[dict[str, Any]]:
        try:
            res = self.client.table("verification_document_types").select("*").order("display_order", desc=False).execute()
            return unwrap_response(res) or []
        except Exception as e:
            logger.warning(f"Could not load verification_document_types: {e}")
            return []

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

    def list_pending(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.list_applications(status_filter="pending", limit=limit)

    def count_metrics(self) -> dict[str, int]:
        self._ensure_pending_applications()
        counts = {"all": 0, "pending": 0, "verified": 0, "rejected": 0}
        try:
            res = self.table().select("id,status").execute()
            rows = unwrap_response(res) or []
            counts["all"] = len(rows)
            for r in rows:
                st = str(r.get("status", "")).upper()
                if st in ["PENDING_REVIEW", "INCOMPLETE"]:
                    counts["pending"] += 1
                elif st == "VERIFIED":
                    counts["verified"] += 1
                elif st == "REJECTED":
                    counts["rejected"] += 1
        except Exception as e:
            logger.warning(f"Error calculating verification counts: {e}")
        return counts

    def list_applications(self, status_filter: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_pending_applications()
        query = (
            self.table()
            .select("id,cleaner_id,status,submitted_at,rejection_reason,cleaner_profiles(id,user_id,overall_rating,availability_status,subscription_status,profiles(id,first_name,last_name,phone,profile_image_path))")
        )

        filter_key = (status_filter or "").lower()
        if filter_key == "pending":
            query = query.in_("status", ["PENDING_REVIEW", "INCOMPLETE"])
        elif filter_key == "verified":
            query = query.eq("status", "VERIFIED")
        elif filter_key == "rejected":
            query = query.eq("status", "REJECTED")

        response = query.order("submitted_at", desc=False).limit(limit).execute()
        applications = unwrap_response(response) or []
        doc_types = self.list_document_types()
        doc_types_by_id = {dt["id"]: dt for dt in doc_types if isinstance(dt, dict) and "id" in dt}

        # Auth lookup for email & metadata fallback
        auth_users_by_id: dict[str, Any] = {}
        try:
            auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
            if auth_admin:
                users_resp = auth_admin.list_users(page=1, per_page=1000)
                users_list = users_resp if isinstance(users_resp, list) else getattr(users_resp, "users", [])
                for u in users_list:
                    uid = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
                    if uid:
                        auth_users_by_id[str(uid)] = u
        except Exception:
            pass

        for app in applications:
            cleaner_id = app.get("cleaner_id")
            app_id = app.get("id")
            cp = app.get("cleaner_profiles") or {}
            prof = cp.get("profiles") or {}
            user_id = str(cp.get("user_id") or "")

            # Attach auth user metadata if available
            auth_u = auth_users_by_id.get(user_id)
            meta: dict[str, Any] = {}
            if auth_u:
                app["auth_email"] = getattr(auth_u, "email", None) or (auth_u.get("email") if isinstance(auth_u, dict) else None)
                meta_raw = getattr(auth_u, "user_metadata", None) or (auth_u.get("user_metadata") if isinstance(auth_u, dict) else {})
                meta = meta_raw if isinstance(meta_raw, dict) else {}
                app["identity_number"] = meta.get("identityNumber") or meta.get("idNumber") or meta.get("id_number")
                app["address"] = meta.get("address") or meta.get("location")
            else:
                app["auth_email"] = None
                app["identity_number"] = None
                app["address"] = None

            # Fetch documents
            docs = []
            if cleaner_id:
                try:
                    doc_res = (
                        self.client.table("cleaner_verification_documents")
                        .select("*")
                        .or_(f"cleaner_id.eq.{cleaner_id},application_id.eq.{app_id}")
                        .order("created_at", desc=True)
                        .execute()
                    )
                    docs_raw = unwrap_response(doc_res) or []
                    for doc in docs_raw:
                        dt_id = doc.get("document_type_id")
                        dt_info = doc_types_by_id.get(dt_id, {})
                        doc["document_type_name"] = dt_info.get("name") or "Verification Document"
                        doc["document_type_description"] = dt_info.get("description")
                        doc["document_type_required"] = dt_info.get("required", True)

                        doc_id = doc.get("id")
                        doc["download_url"] = f"/verification/documents/{doc_id}/download"
                        doc["preview_url"] = f"/verification/documents/{doc_id}/view"

                        # Check cache for signed URL without making network roundtrips
                        storage_path = doc.get("storage_path")
                        cache_key = ("cleaner-verification-documents", storage_path) if storage_path else None
                        if cache_key and cache_key in _signed_url_cache:
                            doc["signed_url"] = _signed_url_cache[cache_key][1]
                        else:
                            doc["signed_url"] = None

                        # Check if mime_type is image or PDF (check MIME and file extension)
                        mime = (doc.get("mime_type") or "").lower()
                        fn = (doc.get("original_file_name") or doc.get("storage_path") or "").lower()
                        doc["is_pdf"] = "pdf" in mime or fn.endswith(".pdf")
                        doc["is_image"] = "image" in mime or fn.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"))

                        # Format file size for display
                        sz = doc.get("file_size") or 0
                        if sz < 1024 * 1024:
                            doc["file_size_display"] = f"{round(sz / 1024, 1)} KB" if sz else "0 KB"
                        else:
                            doc["file_size_display"] = f"{round(sz / (1024 * 1024), 2)} MB"

                        docs.append(doc)
                except Exception as e:
                    logger.warning(f"Error loading cleaner documents for {cleaner_id}: {e}")

            app["documents"] = docs

            # Profile photo
            meta_img = meta.get("profileImagePath") or meta.get("profile_image_path") if isinstance(meta, dict) else None
            img_path = prof.get("profile_image_path") or meta_img
            if img_path:
                if str(img_path).startswith("data:image"):
                    prof["profile_image_url"] = str(img_path)
                else:
                    cache_key = ("profile-images", str(img_path))
                    prof["profile_image_url"] = _signed_url_cache[cache_key][1] if cache_key in _signed_url_cache else None
            else:
                prof["profile_image_url"] = None

            # Calculate document completion statistics
            uploaded_type_ids = {d.get("document_type_id") for d in docs if d.get("status") != "REPLACED"}
            required_count = sum(1 for dt in doc_types if dt.get("required", True))
            uploaded_required_count = sum(1 for dt in doc_types if dt.get("required", True) and dt.get("id") in uploaded_type_ids)
            app["stats"] = {
                "total_documents": len(docs),
                "required_total": required_count,
                "required_uploaded": uploaded_required_count,
                "is_complete": uploaded_required_count >= required_count and required_count > 0,
            }
            app["all_document_types"] = doc_types

            # Group documents by document_type_id for deterministic template display
            docs_by_type: dict[str, list[dict[str, Any]]] = {}
            for d in docs:
                dt_id = str(d.get("document_type_id") or "")
                docs_by_type.setdefault(dt_id, []).append(d)

            # Build slots for predefined document types (primary document for each type)
            doc_slots = []
            known_type_ids = set()
            for dt in doc_types:
                dt_id = str(dt.get("id") or "")
                known_type_ids.add(dt_id)
                type_docs = [d for d in docs_by_type.get(dt_id, []) if d.get("status") != "REPLACED"]
                primary_doc = type_docs[0] if type_docs else None
                prev_docs = type_docs[1:] if len(type_docs) > 1 else []
                doc_slots.append({
                    "doc_type": dt,
                    "document": primary_doc,
                    "previous_documents": prev_docs,
                    "has_uploaded": primary_doc is not None,
                })

            # Custom documents: ONLY documents whose type is not in standard document types
            custom_docs = [d for d in docs if str(d.get("document_type_id") or "") not in known_type_ids]

            app["doc_slots"] = doc_slots
            app["custom_documents"] = custom_docs

        return applications

    def _ensure_pending_applications(self) -> None:
        try:
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()

            cp_res = self.client.table("cleaner_profiles").select("id,verification_status").execute()
            cp_rows = unwrap_response(cp_res) or []
            va_res = self.table().select("id,cleaner_id,status").execute()
            va_rows = unwrap_response(va_res) or []
            existing_cids = {r.get("cleaner_id"): r for r in va_rows if isinstance(r, dict) and "cleaner_id" in r}

            # Query all documents to detect cleaners who actually uploaded documents
            doc_res = self.client.table("cleaner_verification_documents").select("id,cleaner_id,application_id,status").execute()
            doc_rows = unwrap_response(doc_res) or []
            cleaners_with_docs = {d["cleaner_id"] for d in doc_rows if d.get("cleaner_id") and d.get("status") != "REPLACED"}

            for cp in cp_rows:
                cid = cp.get("id")
                if not cid:
                    continue
                has_docs = cid in cleaners_with_docs
                curr_cp_stat = cp.get("verification_status")

                # If cleaner has uploaded documents, their status MUST NOT be NOT_SUBMITTED!
                if has_docs and curr_cp_stat in [None, "", "NOT_SUBMITTED"]:
                    try:
                        self.client.table("cleaner_profiles").update({"verification_status": "PENDING_REVIEW"}).eq("id", cid).execute()
                        curr_cp_stat = "PENDING_REVIEW"
                    except Exception:
                        pass

                if cid not in existing_cids:
                    v_stat = curr_cp_stat or ("PENDING_REVIEW" if has_docs else "NOT_SUBMITTED")
                    if v_stat not in ["NOT_SUBMITTED", "INCOMPLETE", "PENDING_REVIEW", "VERIFIED", "REJECTED", "SUSPENDED"]:
                        v_stat = "PENDING_REVIEW"
                    try:
                        payload = {"cleaner_id": cid, "status": v_stat, "submitted_at": now_iso}
                        ins_res = self.table().insert(payload).execute()
                        ins_rows = unwrap_response(ins_res) or []
                        if ins_rows:
                            existing_cids[cid] = ins_rows[0]
                    except Exception:
                        pass
                else:
                    # Application exists: if cleaner has documents but app is NOT_SUBMITTED, update app to PENDING_REVIEW
                    app_record = existing_cids[cid]
                    app_stat = app_record.get("status")
                    if has_docs and app_stat == "NOT_SUBMITTED":
                        try:
                            self.table().update({"status": "PENDING_REVIEW", "submitted_at": now_iso}).eq("id", app_record.get("id")).execute()
                            app_record["status"] = "PENDING_REVIEW"
                        except Exception:
                            pass

            # Link unlinked documents
            try:
                unlinked_docs = [d for d in doc_rows if not d.get("application_id")]
                for doc in unlinked_docs:
                    cid = doc.get("cleaner_id")
                    app_rec = existing_cids.get(cid)
                    app_id = app_rec.get("id") if isinstance(app_rec, dict) else app_rec
                    if app_id:
                        self.client.table("cleaner_verification_documents").update({"application_id": app_id}).eq("id", doc.get("id")).execute()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Error in _ensure_pending_applications: {e}")

    def set_application_status(self, application_id: str, status: str, admin_profile_id: str | None = None, reason: str | None = None) -> dict[str, Any] | None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "status": status,
            "rejection_reason": reason,
            "reviewed_at": now_iso,
            "updated_at": now_iso,
        }
        if admin_profile_id:
            payload["reviewed_by"] = admin_profile_id
        try:
            response = self.table().update(payload).eq("id", application_id).execute()
            data = unwrap_response(response) or []
            return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        except Exception as exc:
            logger.warning(f"Error setting application status with reviewed_by: {exc}. Retrying without reviewed_by.")
            payload.pop("reviewed_by", None)
            response = self.table().update(payload).eq("id", application_id).execute()
            data = unwrap_response(response) or []
            return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)

    def set_document_status(self, document_id: str, status: str, admin_profile_id: str | None = None, reason: str | None = None) -> dict[str, Any] | None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "status": status,
            "rejection_reason": reason,
            "reviewed_at": now_iso,
            "updated_at": now_iso,
        }
        if admin_profile_id:
            payload["reviewed_by"] = admin_profile_id
        try:
            response = self.client.table("cleaner_verification_documents").update(payload).eq("id", document_id).execute()
            data = unwrap_response(response) or []
            return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        except Exception as exc:
            logger.warning(f"Error setting document status with reviewed_by: {exc}. Retrying without reviewed_by.")
            payload.pop("reviewed_by", None)
            response = self.client.table("cleaner_verification_documents").update(payload).eq("id", document_id).execute()
            data = unwrap_response(response) or []
            return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)

    def _ensure_pending_client_applications(self) -> None:
        try:
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()

            cp_res = self.client.table("client_profiles").select("id,user_id,verification_status").execute()
            cp_rows = unwrap_response(cp_res)
            if not isinstance(cp_rows, list) or not cp_rows:
                return

            cva_res = self.client.table("client_verification_applications").select("id,client_id,status").execute()
            cva_rows = unwrap_response(cva_res)
            cva_rows = cva_rows if isinstance(cva_rows, list) else []
            existing_cva = {str(r.get("client_id")): r for r in cva_rows if isinstance(r, dict) and "client_id" in r}

            doc_res = self.client.table("client_verification_documents").select("id,client_id,application_id,status").execute()
            doc_rows = unwrap_response(doc_res)
            doc_rows = doc_rows if isinstance(doc_rows, list) else []
            clients_with_docs = {str(d["client_id"]) for d in doc_rows if isinstance(d, dict) and d.get("client_id") and d.get("status") != "REPLACED"}

            for cp in cp_rows:
                if not isinstance(cp, dict):
                    continue
                cid = str(cp.get("id") or "")
                if not cid:
                    continue
                has_docs = cid in clients_with_docs
                curr_stat = cp.get("verification_status")

                if has_docs and curr_stat in [None, "", "NOT_SUBMITTED"]:
                    try:
                        self.client.table("client_profiles").update({"verification_status": "PENDING_REVIEW", "submitted_at": now_iso}).eq("id", cid).execute()
                        curr_stat = "PENDING_REVIEW"
                    except Exception:
                        pass

                if cid not in existing_cva:
                    v_stat = curr_stat or ("PENDING_REVIEW" if has_docs else "NOT_SUBMITTED")
                    if v_stat not in ["NOT_SUBMITTED", "INCOMPLETE", "PENDING_REVIEW", "VERIFIED", "REJECTED", "SUSPENDED"]:
                        v_stat = "PENDING_REVIEW"
                    try:
                        payload = {"client_id": cid, "status": v_stat, "submitted_at": now_iso}
                        ins = self.client.table("client_verification_applications").insert(payload).execute()
                        ins_rows = unwrap_response(ins)
                        if isinstance(ins_rows, list) and ins_rows:
                            existing_cva[cid] = ins_rows[0]
                    except Exception:
                        pass
                else:
                    app_record = existing_cva[cid]
                    app_stat = app_record.get("status") if isinstance(app_record, dict) else None
                    if has_docs and app_stat == "NOT_SUBMITTED":
                        try:
                            self.client.table("client_verification_applications").update({"status": "PENDING_REVIEW", "submitted_at": now_iso}).eq("id", app_record.get("id")).execute()
                            if isinstance(app_record, dict):
                                app_record["status"] = "PENDING_REVIEW"
                        except Exception:
                            pass

            unlinked_docs = [d for d in doc_rows if isinstance(d, dict) and not d.get("application_id")]
            for doc in unlinked_docs:
                cid = str(doc.get("client_id") or "")
                app_rec = existing_cva.get(cid)
                app_id = app_rec.get("id") if isinstance(app_rec, dict) else app_rec
                if app_id:
                    try:
                        self.client.table("client_verification_documents").update({"application_id": app_id}).eq("id", doc.get("id")).execute()
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Error in _ensure_pending_client_applications: {e}")

    def count_client_metrics(self) -> dict[str, int]:
        self._ensure_pending_client_applications()
        counts = {"all": 0, "pending": 0, "verified": 0, "rejected": 0, "unverified": 0}
        try:
            cp_res = self.client.table("client_profiles").select("id,verification_status").execute()
            cp_rows = unwrap_response(cp_res)
            if not isinstance(cp_rows, list) or not cp_rows:
                p_res = self.client.table("profiles").select("id").eq("role", "CLIENT").execute()
                p_rows = unwrap_response(p_res)
                if isinstance(p_rows, list) and p_rows:
                    counts["all"] = len(p_rows)
                    client_uids = {str(r["id"]) for r in p_rows if isinstance(r, dict) and "id" in r}
                    civ_res = self.client.table("client_identity_verifications").select("user_id,status").execute()
                    civ_rows = unwrap_response(civ_res)
                    civ_map = {str(r["user_id"]): str(r.get("status", "")).lower() for r in civ_rows if isinstance(r, dict) and "user_id" in r} if isinstance(civ_rows, list) else {}
                    rev_res = self.client.table("identity_verification_reviews").select("user_id,status").eq("status", "pending").execute()
                    rev_rows = unwrap_response(rev_res)
                    pending_rev = {str(r["user_id"]) for r in rev_rows if isinstance(r, dict) and "user_id" in r} if isinstance(rev_rows, list) else set()
                    for uid in client_uids:
                        v_stat = civ_map.get(uid, "unverified")
                        if uid in pending_rev or v_stat in ["pending", "manual_review"]:
                            counts["pending"] += 1
                        elif v_stat == "verified":
                            counts["verified"] += 1
                        elif v_stat == "rejected":
                            counts["rejected"] += 1
                        else:
                            counts["unverified"] += 1
                    return counts
                return counts

            counts["all"] = len(cp_rows)
            for r in cp_rows:
                if not isinstance(r, dict):
                    continue
                st = str(r.get("verification_status") or "").upper()
                if st in ["PENDING_REVIEW", "INCOMPLETE"]:
                    counts["pending"] += 1
                elif st == "VERIFIED":
                    counts["verified"] += 1
                elif st == "REJECTED":
                    counts["rejected"] += 1
                else:
                    counts["unverified"] += 1
        except Exception as e:
            logger.warning(f"Error calculating client verification metrics: {e}")
        return counts

    def list_client_applications(self, status_filter: str | None = None, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_pending_client_applications()
        try:
            p_query = (
                self.client.table("client_profiles")
                .select("id,user_id,verification_status,submitted_at,verified_at,verified_by,rejection_reason,profiles(id,first_name,last_name,phone,profile_image_path,account_status,created_at,role)")
            )
            cp_res = p_query.execute()
            cp_rows = unwrap_response(cp_res)
            if not isinstance(cp_rows, list) or not cp_rows:
                p_res = self.client.table("profiles").select("id,first_name,last_name,phone,profile_image_path,account_status,created_at,role").eq("role", "CLIENT").order("created_at", desc=True).limit(limit * 2).execute()
                p_rows = unwrap_response(p_res)
                if not isinstance(p_rows, list) or not p_rows:
                    return []
                uids = [str(p["id"]) for p in p_rows if isinstance(p, dict) and "id" in p]
                cp_sub = self.client.table("client_profiles").select("*").in_("user_id", uids).execute()
                cp_sub_rows = unwrap_response(cp_sub)
                cp_map = {str(c["user_id"]): c for c in cp_sub_rows if isinstance(c, dict) and "user_id" in c} if isinstance(cp_sub_rows, list) else {}
                cp_rows = [
                    {
                        "id": cp_map.get(str(p["id"]), {}).get("id", f"cp-{p['id']}"),
                        "user_id": str(p["id"]),
                        "verification_status": cp_map.get(str(p["id"]), {}).get("verification_status", "NOT_SUBMITTED"),
                        "profiles": p,
                    }
                    for p in p_rows if isinstance(p, dict) and "id" in p
                ]

            client_ids = [str(r["id"]) for r in cp_rows if isinstance(r, dict) and "id" in r]
            user_ids = [str(r["user_id"]) for r in cp_rows if isinstance(r, dict) and "user_id" in r]

            addr_by_uid: dict[str, str] = {}
            try:
                addr_res = self.client.table("addresses").select("user_id,address_line_1,suburb,city,label").in_("user_id", user_ids).execute()
                addr_rows = unwrap_response(addr_res)
                if isinstance(addr_rows, list):
                    for a in addr_rows:
                        if isinstance(a, dict) and "user_id" in a:
                            sub = a.get("suburb") or a.get("address_line_1") or a.get("city") or ""
                            if sub:
                                addr_by_uid[str(a["user_id"])] = sub
            except Exception as e:
                logger.warning(f"Error loading addresses: {e}")

            docs_by_client_id: dict[str, list[dict[str, Any]]] = {}
            try:
                doc_res = self.client.table("client_verification_documents").select("*").in_("client_id", client_ids).order("created_at", desc=False).execute()
                doc_rows = unwrap_response(doc_res)
                if isinstance(doc_rows, list):
                    for d in doc_rows:
                        if isinstance(d, dict) and "client_id" in d:
                            cid = str(d["client_id"])
                            d_path = str(d.get("storage_path") or "")
                            d["preview_url"] = self.get_signed_url("client-verification-documents", d_path) if d_path else None
                            docs_by_client_id.setdefault(cid, []).append(d)
            except Exception as e:
                logger.warning(f"Error loading client_verification_documents: {e}")

            app_by_client_id: dict[str, dict[str, Any]] = {}
            try:
                cva_res = self.client.table("client_verification_applications").select("*").in_("client_id", client_ids).order("submitted_at", desc=True).execute()
                cva_rows = unwrap_response(cva_res)
                if isinstance(cva_rows, list):
                    for cva in cva_rows:
                        if isinstance(cva, dict) and "client_id" in cva:
                            cid = str(cva["client_id"])
                            if cid not in app_by_client_id:
                                app_by_client_id[cid] = cva
            except Exception as e:
                logger.warning(f"Error loading client_verification_applications: {e}")

            # Legacy CIV fallback
            civ_by_uid: dict[str, Any] = {}
            try:
                civ_res = self.client.table("client_identity_verifications").select("*").in_("user_id", user_ids).execute()
                civ_rows = unwrap_response(civ_res)
                if isinstance(civ_rows, list):
                    for civ in civ_rows:
                        if isinstance(civ, dict) and "user_id" in civ:
                            civ_by_uid[str(civ["user_id"])] = civ
            except Exception:
                pass

            # Legacy Reviews fallback
            rev_by_uid: dict[str, Any] = {}
            try:
                rev_res = self.client.table("identity_verification_reviews").select("*").in_("user_id", user_ids).order("created_at", desc=True).execute()
                rev_rows = unwrap_response(rev_res)
                if isinstance(rev_rows, list):
                    for rev in rev_rows:
                        if isinstance(rev, dict) and "user_id" in rev:
                            uid_str = str(rev["user_id"])
                            if uid_str not in rev_by_uid:
                                rev_by_uid[uid_str] = rev
            except Exception:
                pass

            auth_users_by_id: dict[str, Any] = {}
            try:
                auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
                if auth_admin:
                    users_resp = auth_admin.list_users(page=1, per_page=1000)
                    users_list = users_resp if isinstance(users_resp, list) else getattr(users_resp, "users", [])
                    if isinstance(users_list, list):
                        for u in users_list:
                            uid = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
                            if uid:
                                auth_users_by_id[str(uid)] = u
            except Exception:
                pass

            client_apps: list[dict[str, Any]] = []
            for cp in cp_rows:
                if not isinstance(cp, dict):
                    continue
                cpid = str(cp.get("id") or "")
                uid = str(cp.get("user_id") or "")
                prof = cp.get("profiles") or {}
                auth_u = auth_users_by_id.get(uid)
                meta: dict[str, Any] = {}
                auth_email = None
                if auth_u:
                    auth_email = getattr(auth_u, "email", None) or (auth_u.get("email") if isinstance(auth_u, dict) else None)
                    meta_raw = getattr(auth_u, "user_metadata", None) or (auth_u.get("user_metadata") if isinstance(auth_u, dict) else {})
                    meta = meta_raw if isinstance(meta_raw, dict) else {}

                client_docs = docs_by_client_id.get(cpid, [])
                id_doc = None
                residence_doc = None
                other_docs = []
                for d in client_docs:
                    if d.get("status") == "REPLACED":
                        continue
                    key = str(d.get("document_type_key") or "").upper()
                    if key == "IDENTITY_DOCUMENT" and not id_doc:
                        id_doc = d
                    elif key == "PROOF_OF_RESIDENCE" and not residence_doc:
                        residence_doc = d
                    else:
                        other_docs.append(d)

                cva = app_by_client_id.get(cpid, {})
                cva_status = str(cva.get("status") or "").upper()
                cp_vstatus = str(cp.get("verification_status") or "").upper()
                civ = civ_by_uid.get(uid, {})
                civ_status = str(civ.get("status") or "").lower()
                rev = rev_by_uid.get(uid, {})
                rev_status = str(rev.get("status") or "").lower()

                if cp_vstatus == "VERIFIED" or cva_status == "VERIFIED" or civ_status == "verified":
                    eff_status = "VERIFIED"
                elif cp_vstatus == "REJECTED" or cva_status == "REJECTED" or civ_status == "rejected" or rev_status == "rejected":
                    eff_status = "REJECTED"
                elif cp_vstatus in ["PENDING_REVIEW", "INCOMPLETE"] or cva_status in ["PENDING_REVIEW", "INCOMPLETE"] or rev_status == "pending" or len(client_docs) > 0:
                    eff_status = "PENDING_REVIEW"
                else:
                    eff_status = "UNVERIFIED"

                img_path = prof.get("profile_image_path") or meta.get("profileImagePath")
                profile_image_url = None
                if img_path:
                    if str(img_path).startswith("data:image") or str(img_path).startswith("http"):
                        profile_image_url = str(img_path)
                    else:
                        profile_image_url = self.get_signed_url("profile-images", str(img_path))

                id_number = meta.get("identityNumber") or meta.get("idNumber") or meta.get("id_number")
                address = addr_by_uid.get(uid) or meta.get("address") or meta.get("location")

                item: dict[str, Any] = {
                    "id": uid,
                    "user_id": uid,
                    "client_profile_id": cpid,
                    "first_name": prof.get("first_name", ""),
                    "last_name": prof.get("last_name", ""),
                    "phone": prof.get("phone", ""),
                    "email": auth_email or prof.get("email"),
                    "auth_email": auth_email,
                    "account_status": prof.get("account_status", "PENDING"),
                    "status": eff_status,
                    "status_raw": cp_vstatus,
                    "verified_at": cp.get("verified_at"),
                    "submitted_at": cp.get("submitted_at") or cva.get("submitted_at"),
                    "rejection_reason": cp.get("rejection_reason") or cva.get("rejection_reason"),
                    "identity_number": id_number,
                    "address": address,
                    "created_at": prof.get("created_at"),
                    "profile_image_url": profile_image_url,
                    "id_document": id_doc,
                    "proof_of_residence": residence_doc,
                    "other_documents": other_docs,
                    "all_documents": client_docs,
                    "has_id_doc": id_doc is not None,
                    "has_residence_doc": residence_doc is not None,
                    "is_complete": (id_doc is not None and residence_doc is not None),
                    "review": rev if rev else None,
                }
                client_apps.append(item)

            f_key = (status_filter or "").lower()
            if f_key == "pending":
                client_apps = [c for c in client_apps if c["status"] == "PENDING_REVIEW"]
            elif f_key == "verified":
                client_apps = [c for c in client_apps if c["status"] == "VERIFIED"]
            elif f_key == "rejected":
                client_apps = [c for c in client_apps if c["status"] == "REJECTED"]
            elif f_key == "unverified":
                client_apps = [c for c in client_apps if c["status"] == "UNVERIFIED"]

            if search:
                s_term = search.strip().lower()
                client_apps = [
                    c for c in client_apps
                    if s_term in (c.get("first_name", "") + " " + c.get("last_name", "")).lower()
                    or s_term in str(c.get("email", "")).lower()
                    or s_term in str(c.get("phone", "")).lower()
                    or s_term in str(c.get("identity_number", "")).lower()
                    or s_term in str(c.get("user_id", "")).lower()
                ]

            return client_apps[:limit]
        except Exception as e:
            logger.exception(f"Error in list_client_applications: {e}")
            return []

    def set_client_verification_status(
        self,
        user_id: str,
        status: str,
        admin_user_id: str,
        admin_profile_id: str | None = None,
        reason: str | None = None,
        evidence_reference: str | None = None,
    ) -> None:
        from datetime import datetime, timezone, timedelta
        now_iso = datetime.now(timezone.utc).isoformat()
        norm_status = status.lower()

        cpid = None
        try:
            cp_res = self.client.table("client_profiles").select("id").eq("user_id", user_id).limit(1).execute()
            cp_rows = unwrap_response(cp_res)
            if isinstance(cp_rows, list) and cp_rows:
                cpid = str(cp_rows[0].get("id"))
        except Exception as e:
            logger.warning(f"Could not load client_profile for user {user_id}: {e}")

        if norm_status == "verified":
            expires_iso = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()

            try:
                cp_update: dict[str, Any] = {
                    "verification_status": "VERIFIED",
                    "verified_at": now_iso,
                    "rejection_reason": None,
                    "updated_at": now_iso,
                }
                if admin_profile_id:
                    cp_update["verified_by"] = admin_profile_id
                self.client.table("client_profiles").update(cp_update).eq("user_id", user_id).execute()
            except Exception as e:
                logger.warning(f"Error updating client_profiles to VERIFIED: {e}")

            try:
                self.client.table("profiles").update({
                    "account_status": "ACTIVE",
                    "onboarding_step": "complete",
                    "onboarding_completed_at": now_iso,
                    "updated_at": now_iso,
                }).eq("id", user_id).execute()
            except Exception as e:
                logger.warning(f"Error updating profiles to ACTIVE: {e}")

            if cpid:
                try:
                    cva_update: dict[str, Any] = {
                        "status": "VERIFIED",
                        "reviewed_at": now_iso,
                        "rejection_reason": None,
                        "updated_at": now_iso,
                    }
                    if admin_profile_id:
                        cva_update["reviewed_by"] = admin_profile_id
                    self.client.table("client_verification_applications").update(cva_update).eq("client_id", cpid).execute()
                except Exception as e:
                    logger.warning(f"Error updating client_verification_applications: {e}")

                try:
                    doc_update: dict[str, Any] = {
                        "status": "APPROVED",
                        "reviewed_at": now_iso,
                        "rejection_reason": None,
                        "updated_at": now_iso,
                    }
                    if admin_profile_id:
                        doc_update["reviewed_by"] = admin_profile_id
                    self.client.table("client_verification_documents").update(doc_update).eq("client_id", cpid).neq("status", "REPLACED").execute()
                except Exception as e:
                    logger.warning(f"Error updating client_verification_documents to APPROVED: {e}")

            try:
                civ_payload = {
                    "user_id": user_id,
                    "status": "verified",
                    "method": "manual",
                    "verified_at": now_iso,
                    "expires_at": expires_iso,
                    "policy_version": "manual_admin",
                    "updated_at": now_iso,
                }
                self.client.table("client_identity_verifications").upsert(civ_payload, on_conflict="user_id").execute()
            except Exception as e:
                logger.warning(f"Error upserting client_identity_verifications: {e}")

            try:
                auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
                if auth_admin:
                    auth_admin.update_user_by_id(user_id, {
                        "user_metadata": {
                            "verificationStatus": "VERIFIED",
                            "verification_status": "VERIFIED",
                            "accountStatus": "ACTIVE",
                            "account_status": "ACTIVE",
                        }
                    })
            except Exception as e:
                logger.warning(f"Error updating auth user metadata: {e}")

            try:
                self.client.table("notifications").insert({
                    "user_id": user_id,
                    "type": "CLIENT_ACCOUNT_ACTIVATED",
                    "title": "Account Verified & Activated! 🎉",
                    "body": "Your Proof of Residence and ID Copy have been verified by an administrator. Your CleanLink account is now active!",
                    "data": {"clientId": cpid, "status": "VERIFIED", "accountStatus": "ACTIVE"}
                }).execute()
            except Exception as e:
                logger.warning(f"Error inserting activation notification: {e}")

        elif norm_status == "rejected":
            try:
                self.client.table("client_profiles").update({
                    "verification_status": "REJECTED",
                    "rejection_reason": reason or "Your verification documents require attention. Please re-upload clear copies.",
                    "updated_at": now_iso,
                }).eq("user_id", user_id).execute()
            except Exception as e:
                logger.warning(f"Error updating client_profiles to REJECTED: {e}")

            try:
                self.client.table("profiles").update({
                    "account_status": "PENDING",
                    "updated_at": now_iso,
                }).eq("id", user_id).execute()
            except Exception as e:
                logger.warning(f"Error updating profiles to PENDING: {e}")

            if cpid:
                try:
                    cva_update = {
                        "status": "REJECTED",
                        "reviewed_at": now_iso,
                        "rejection_reason": reason or "Documents rejected after administrative review",
                        "updated_at": now_iso,
                    }
                    if admin_profile_id:
                        cva_update["reviewed_by"] = admin_profile_id
                    self.client.table("client_verification_applications").update(cva_update).eq("client_id", cpid).execute()
                except Exception as e:
                    logger.warning(f"Error updating client_verification_applications to REJECTED: {e}")

                try:
                    doc_update = {
                        "status": "REJECTED",
                        "reviewed_at": now_iso,
                        "rejection_reason": reason or "Rejected after administrative review",
                        "updated_at": now_iso,
                    }
                    if admin_profile_id:
                        doc_update["reviewed_by"] = admin_profile_id
                    self.client.table("client_verification_documents").update(doc_update).eq("client_id", cpid).neq("status", "REPLACED").execute()
                except Exception as e:
                    logger.warning(f"Error updating client_verification_documents to REJECTED: {e}")

            try:
                civ_payload = {
                    "user_id": user_id,
                    "status": "rejected",
                    "updated_at": now_iso,
                }
                self.client.table("client_identity_verifications").upsert(civ_payload, on_conflict="user_id").execute()
            except Exception as e:
                logger.warning(f"Error upserting client_identity_verifications: {e}")

            try:
                auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
                if auth_admin:
                    auth_admin.update_user_by_id(user_id, {
                        "user_metadata": {
                            "verificationStatus": "REJECTED",
                            "verification_status": "REJECTED",
                            "accountStatus": "PENDING",
                            "account_status": "PENDING",
                        }
                    })
            except Exception as e:
                logger.warning(f"Error updating auth user metadata: {e}")

            try:
                self.client.table("notifications").insert({
                    "user_id": user_id,
                    "type": "CLIENT_VERIFICATION_REJECTED",
                    "title": "Document Verification Update ⚠️",
                    "body": reason or "Some of your uploaded documents could not be verified. Please re-upload your Proof of Residence and ID Copy.",
                    "data": {"clientId": cpid, "status": "REJECTED", "reason": reason}
                }).execute()
            except Exception as e:
                logger.warning(f"Error inserting rejection notification: {e}")

        elif norm_status == "unverified":
            try:
                self.client.table("client_profiles").update({
                    "verification_status": "NOT_SUBMITTED",
                    "verified_at": None,
                    "verified_by": None,
                    "rejection_reason": reason,
                    "updated_at": now_iso,
                }).eq("user_id", user_id).execute()
            except Exception as e:
                logger.warning(f"Error updating client_profiles to NOT_SUBMITTED: {e}")

            try:
                self.client.table("profiles").update({
                    "account_status": "PENDING",
                    "updated_at": now_iso,
                }).eq("id", user_id).execute()
            except Exception as e:
                logger.warning(f"Error updating profiles: {e}")

            try:
                civ_payload = {
                    "user_id": user_id,
                    "status": "unverified",
                    "verified_at": None,
                    "expires_at": None,
                    "updated_at": now_iso,
                }
                self.client.table("client_identity_verifications").upsert(civ_payload, on_conflict="user_id").execute()
            except Exception as e:
                logger.warning(f"Error upserting client_identity_verifications: {e}")
