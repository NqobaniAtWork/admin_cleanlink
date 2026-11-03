from typing import Any

from app.core.supabase import unwrap_response
from app.repositories.base import SupabaseRepository


class SettingsRepository(SupabaseRepository):
    table_name = "system_settings"

    def list_settings(self) -> list[dict[str, Any]]:
        response = self.table().select("key,value_type,value_json,description,updated_at").order("key").execute()
        return unwrap_response(response) or []

    def update_setting(self, key: str, value_json: Any) -> dict[str, Any] | None:
        response = self.table().update({"value_json": value_json}).eq("key", key).execute()
        rows = unwrap_response(response) or []
        return rows[0] if rows else None