from typing import Any


class NotificationService:
    def __init__(self, client: Any) -> None:
        self.client = client

    def create_admin_notification(self, user_id: str, title: str, body: str, data: dict[str, Any] | None = None) -> None:
        self.client.table("notifications").insert(
            {"user_id": user_id, "type": "admin_notice", "title": title, "body": body, "data": data or {}}
        ).execute()