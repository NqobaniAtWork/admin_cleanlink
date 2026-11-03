from typing import Any
from uuid import UUID

from app.core.supabase import unwrap_response


class ClientIdentityAdminService:
    """Metadata-only lists and service-only atomic database decisions."""
    def __init__(self, client: Any) -> None:
        self.client = client

    def overview(self, page: int = 0) -> dict[str, Any]:
        start = max(0, page) * 50
        states = unwrap_response(self.client.table("client_identity_verifications").select(
            "user_id,status,method,verified_at,expires_at,provider,provider_subject,policy_version"
        ).order("updated_at", desc=True).range(start, start + 49).execute()) or []
        reviews = unwrap_response(self.client.table("identity_verification_reviews").select(
            "id,user_id,status,reason,client_note,assigned_admin,evidence_reference,process_version,decision_reason,created_at,decided_at"
        ).order("created_at", desc=True).range(start, start + 49).execute()) or []
        attempts = unwrap_response(self.client.table("identity_verification_attempts").select(
            "id,user_id,status,failure_category,created_at,completed_at"
        ).order("created_at", desc=True).range(start, start + 49).execute()) or []
        deletions = unwrap_response(self.client.table("identity_deletion_tasks").select(
            "id,user_id,provider,provider_subject,reason,created_at,completed_at,confirmation_reference"
        ).is_("completed_at", "null").order("created_at").range(start, start + 49).execute()) or []
        return {"states": states, "reviews": reviews, "attempts": attempts, "deletions": deletions}

    def decide(self, review_id: str, admin_user_id: str, approve: bool, evidence_reference: str, reason: str) -> None:
        UUID(review_id)
        UUID(admin_user_id)
        if not 8 <= len(evidence_reference.strip()) <= 200 or not 8 <= len(reason.strip()) <= 1000:
            raise ValueError("Record the approved process evidence reference and decision reason.")
        self.client.rpc("decide_client_identity_review", {
            "p_review_id": review_id, "p_admin_user_id": admin_user_id, "p_approve": approve,
            "p_evidence_reference": evidence_reference.strip(), "p_reason": reason.strip(),
        }).execute()

    def confirm_deletion(self, task_id: str, admin_user_id: str, confirmation_reference: str) -> None:
        UUID(task_id)
        UUID(admin_user_id)
        if not 8 <= len(confirmation_reference.strip()) <= 200:
            raise ValueError("Record the processor's actual purge confirmation reference.")
        self.client.rpc("confirm_identity_provider_deletion", {
            "p_task_id": task_id, "p_admin_user_id": admin_user_id,
            "p_confirmation_reference": confirmation_reference.strip(),
        }).execute()
