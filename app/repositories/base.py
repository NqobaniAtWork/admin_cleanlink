from typing import Any

from app.core.supabase import unwrap_response


class SupabaseRepository:
    """Small repository wrapper around the Supabase Python client."""

    table_name: str

    def __init__(self, client: Any) -> None:
        self.client = client

    def table(self) -> Any:
        return self.client.table(self.table_name)

    def list_recent(self, limit: int = 25, order_by: str = "created_at") -> list[dict[str, Any]]:
        response = self.table().select("*").order(order_by, desc=True).limit(limit).execute()
        return unwrap_response(response) or []

    def count(self, **filters: Any) -> int:
        query = self.table().select("id", count="exact").limit(1)
        for column, value in filters.items():
            query = query.eq(column, value)
        response = query.execute()
        return int(getattr(response, "count", 0) or 0)