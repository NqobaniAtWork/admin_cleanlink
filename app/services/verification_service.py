import logging
from typing import Any

from app.repositories.cleaner_repository import CleanerRepository
from app.repositories.verification_repository import VerificationRepository
from app.schemas.admin_actions import AdminActionCreate
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class VerificationService:
    def __init__(self, client: Any) -> None:
        self.verification = VerificationRepository(client)
        self.cleaners = CleanerRepository(client)
        self.audit = AuditService(client)
        self.client = client

    # --- Cleaner verification methods ---

    def pending_applications(self) -> list[dict[str, Any]]:
        return self.verification.list_pending()

    def list_applications(self, status_filter: str | None = None) -> list[dict[str, Any]]:
        return self.verification.list_applications(status_filter=status_filter)

    def count_metrics(self) -> dict[str, int]:
        return self.verification.count_metrics()

    def approve_cleaner(self, admin_user_id: str, admin_profile_id: str | None, application_id: str, cleaner_id: str, reason: str | None = None) -> None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # 1. Update application status
        self.verification.set_application_status(application_id, "VERIFIED", admin_profile_id, reason)
        
        # 2. Update cleaner profile status
        update_payload: dict[str, Any] = {
            "verification_status": "VERIFIED",
            "availability_status": "AVAILABLE",
            "verified_at": now_iso,
        }
        if admin_profile_id:
            update_payload["verified_by"] = admin_profile_id
        self.cleaners.update_status(cleaner_id, **update_payload)
        
        # 3. Find cleaner user_id and activate in profiles
        cp_data = None
        if hasattr(self.cleaners, "get_by_id"):
            try:
                cp_data = self.cleaners.get_by_id(cleaner_id)
            except Exception:
                pass
        if not cp_data:
            try:
                res = self.client.table("cleaner_profiles").select("user_id").eq("id", cleaner_id).maybe_single().execute()
                cp_data = getattr(res, "data", None) if res else None
            except Exception:
                pass
        cleaner_user_id = str(cp_data.get("user_id") or "") if isinstance(cp_data, dict) else None
        
        if cleaner_user_id:
            try:
                self.client.table("profiles").update({
                    "account_status": "ACTIVE",
                    "onboarding_step": "complete",
                    "onboarding_completed_at": now_iso,
                    "updated_at": now_iso,
                }).eq("id", cleaner_user_id).execute()
            except Exception as e:
                logger.warning(f"Could not update cleaner profiles row to ACTIVE: {e}")

        # 4. Approve all active cleaner documents
        try:
            self.client.table("cleaner_verification_documents").update({
                "status": "APPROVED",
                "reviewed_at": now_iso,
                "reviewed_by": admin_profile_id,
                "updated_at": now_iso,
            }).eq("cleaner_id", cleaner_id).neq("status", "REPLACED").execute()
        except Exception as e:
            logger.warning(f"Could not update cleaner_verification_documents to APPROVED: {e}")

        # 5. Sync Auth user metadata
        if cleaner_user_id:
            try:
                auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
                if auth_admin:
                    auth_admin.update_user_by_id(cleaner_user_id, {
                        "user_metadata": {
                            "verificationStatus": "VERIFIED",
                            "verification_status": "VERIFIED",
                            "accountStatus": "ACTIVE",
                            "account_status": "ACTIVE",
                        }
                    })
            except Exception as e:
                logger.warning(f"Could not update cleaner auth user metadata: {e}")

        # 6. Insert verification review record if table exists
        try:
            review_payload: dict[str, Any] = {
                "cleaner_id": cleaner_id,
                "application_id": application_id,
                "decision": "APPROVED",
                "notes": reason or "Verification approved by administrator",
            }
            if admin_profile_id:
                review_payload["admin_id"] = admin_profile_id
            self.client.table("verification_reviews").insert(review_payload).execute()
        except Exception:
            pass

        # 7. In-app notification
        if cleaner_user_id:
            try:
                self.client.table("notifications").insert({
                    "user_id": cleaner_user_id,
                    "type": "CLEANER_VERIFIED",
                    "title": "Verification Approved! 🎉",
                    "body": "Your documents have been verified by an administrator. You can now go online and accept cleaning requests!",
                    "data": {"status": "VERIFIED", "accountStatus": "ACTIVE"}
                }).execute()
            except Exception:
                pass

        # 8. Audit trail
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="CLEANER_VERIFIED",
                target_type="cleaner_profiles",
                target_id=cleaner_id,
                reason=reason or "Cleaner verification approved by administrator",
                metadata={"application_id": application_id},
            )
        )

    def reject_cleaner(self, admin_user_id: str, admin_profile_id: str | None, application_id: str, cleaner_id: str, reason: str) -> None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Update application status
        self.verification.set_application_status(application_id, "REJECTED", admin_profile_id, reason)
        
        # 2. Update cleaner profile status
        self.cleaners.update_status(cleaner_id, verification_status="REJECTED", availability_status="UNAVAILABLE")
        
        # 3. Find cleaner user_id and activate in profiles
        cp_data = None
        if hasattr(self.cleaners, "get_by_id"):
            try:
                cp_data = self.cleaners.get_by_id(cleaner_id)
            except Exception:
                pass
        if not cp_data:
            try:
                res = self.client.table("cleaner_profiles").select("user_id").eq("id", cleaner_id).maybe_single().execute()
                cp_data = getattr(res, "data", None) if res else None
            except Exception:
                pass
        cleaner_user_id = str(cp_data.get("user_id") or "") if isinstance(cp_data, dict) else None

        # 3. Mark documents as REJECTED
        try:
            self.client.table("cleaner_verification_documents").update({
                "status": "REJECTED",
                "rejection_reason": reason,
                "reviewed_at": now_iso,
                "reviewed_by": admin_profile_id,
                "updated_at": now_iso,
            }).eq("cleaner_id", cleaner_id).neq("status", "REPLACED").execute()
        except Exception as e:
            logger.warning(f"Could not update cleaner_verification_documents to REJECTED: {e}")

        # 4. Set account_status to PENDING in profiles
        if cleaner_user_id:
            try:
                self.client.table("profiles").update({
                    "account_status": "PENDING",
                    "updated_at": now_iso,
                }).eq("id", cleaner_user_id).execute()
            except Exception:
                pass

        # 5. Sync Auth user metadata
        if cleaner_user_id:
            try:
                auth_admin = getattr(getattr(self.client, "auth", None), "admin", None)
                if auth_admin:
                    auth_admin.update_user_by_id(cleaner_user_id, {
                        "user_metadata": {
                            "verificationStatus": "REJECTED",
                            "verification_status": "REJECTED",
                            "accountStatus": "PENDING",
                            "account_status": "PENDING",
                        }
                    })
            except Exception:
                pass

        # 6. Insert verification review record if table exists
        try:
            review_payload: dict[str, Any] = {
                "cleaner_id": cleaner_id,
                "application_id": application_id,
                "decision": "REJECTED",
                "notes": reason or "Verification rejected by administrator",
            }
            if admin_profile_id:
                review_payload["admin_id"] = admin_profile_id
            self.client.table("verification_reviews").insert(review_payload).execute()
        except Exception:
            pass

        # 7. In-app notification
        if cleaner_user_id:
            try:
                self.client.table("notifications").insert({
                    "user_id": cleaner_user_id,
                    "type": "CLEANER_VERIFICATION_REJECTED",
                    "title": "Document Verification Update ⚠️",
                    "body": reason,
                    "data": {"status": "REJECTED", "reason": reason}
                }).execute()
            except Exception:
                pass

        # 8. Audit trail
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="CLEANER_REJECTED",
                target_type="cleaner_profiles",
                target_id=cleaner_id,
                reason=reason,
                metadata={"application_id": application_id},
            )
        )

    def review_document(self, admin_user_id: str, admin_profile_id: str | None, document_id: str, cleaner_id: str, status: str, reason: str | None = None) -> None:
        self.verification.set_document_status(document_id, status, admin_profile_id, reason)
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action=f"DOCUMENT_{status}",
                target_type="cleaner_verification_documents",
                target_id=document_id,
                reason=reason or f"Document marked as {status}",
                metadata={"cleaner_id": cleaner_id, "document_id": document_id, "status": status},
            )
        )

    # --- Client verification methods ---

    def list_client_applications(self, status_filter: str | None = None, search: str = "") -> list[dict[str, Any]]:
        return self.verification.list_client_applications(status_filter=status_filter, search=search)

    def count_client_metrics(self) -> dict[str, int]:
        return self.verification.count_client_metrics()

    def approve_client(
        self,
        admin_user_id: str,
        admin_profile_id: str | None,
        user_id: str,
        reason: str | None = None,
        evidence_reference: str | None = None,
    ) -> None:
        self.verification.set_client_verification_status(
            user_id=user_id,
            status="verified",
            admin_user_id=admin_user_id,
            admin_profile_id=admin_profile_id,
            reason=reason,
            evidence_reference=evidence_reference,
        )
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="CLIENT_VERIFIED",
                target_type="client_profiles",
                target_id=user_id,
                reason=reason or "Client verification approved by administrator",
                metadata={"user_id": user_id, "evidence_reference": evidence_reference},
            )
        )

    def reject_client(
        self,
        admin_user_id: str,
        admin_profile_id: str | None,
        user_id: str,
        reason: str,
    ) -> None:
        self.verification.set_client_verification_status(
            user_id=user_id,
            status="rejected",
            admin_user_id=admin_user_id,
            admin_profile_id=admin_profile_id,
            reason=reason,
        )
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="CLIENT_REJECTED",
                target_type="client_profiles",
                target_id=user_id,
                reason=reason,
                metadata={"user_id": user_id},
            )
        )

    def reset_client(
        self,
        admin_user_id: str,
        admin_profile_id: str | None,
        user_id: str,
        reason: str | None = None,
    ) -> None:
        self.verification.set_client_verification_status(
            user_id=user_id,
            status="unverified",
            admin_user_id=admin_user_id,
            admin_profile_id=admin_profile_id,
            reason=reason,
        )
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="CLIENT_VERIFICATION_RESET",
                target_type="client_profiles",
                target_id=user_id,
                reason=reason or "Client verification reset to unverified",
                metadata={"user_id": user_id},
            )
        )
