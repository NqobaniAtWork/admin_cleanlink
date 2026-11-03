import logging
from typing import Any

from app.schemas.admin_actions import AdminActionCreate

logger = logging.getLogger(__name__)


class AuditService:
    """Writes auditable admin actions; the database trigger creates audit_logs."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def record(self, action: AdminActionCreate) -> dict[str, Any] | None:
        try:
            response = self.client.table("admin_actions").insert(action.model_dump()).execute()
            data = getattr(response, "data", None) or []
            return data[0] if data else None
        except Exception as exc:
            logger.warning(f"Audit record skipped due to error: {exc}")
            return None