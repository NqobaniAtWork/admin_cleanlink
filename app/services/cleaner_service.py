from typing import Any

from app.repositories.cleaner_repository import CleanerRepository
from app.schemas.admin_actions import AdminActionCreate
from app.services.audit_service import AuditService


class CleanerService:
    def __init__(self, client: Any) -> None:
        self.cleaners = CleanerRepository(client)
        self.audit = AuditService(client)

    def list_cleaners(self) -> list[dict[str, Any]]:
        return self.cleaners.list_cleaners()

    def suspend_cleaner(self, admin_user_id: str, cleaner_id: str, reason: str) -> None:
        self.cleaners.update_status(cleaner_id, verification_status="SUSPENDED", availability_status="UNAVAILABLE")
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="ACCOUNT_SUSPENDED",
                target_type="cleaner_profiles",
                target_id=cleaner_id,
                reason=reason,
            )
        )

    def activate_cleaner(self, admin_user_id: str, cleaner_id: str, reason: str = "Administrative activation") -> None:
        self.cleaners.update_status(cleaner_id, verification_status="VERIFIED", availability_status="AVAILABLE")
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="ACCOUNT_ACTIVATED",
                target_type="cleaner_profiles",
                target_id=cleaner_id,
                reason=reason,
            )
        )

    def get_document(self, cleaner_id: str, document_id: str) -> dict[str, Any] | None:
        return self.cleaners.get_cleaner_document(cleaner_id, document_id)

    def get_document_download_url(self, cleaner_id: str, document_id: str) -> str | None:
        doc = self.cleaners.get_cleaner_document(cleaner_id, document_id)
        if not doc:
            return None
        storage_path = doc.get("storage_path")
        if not storage_path:
            return None
        return self.cleaners.get_signed_url("cleaner-verification-documents", storage_path, expires_in=3600)