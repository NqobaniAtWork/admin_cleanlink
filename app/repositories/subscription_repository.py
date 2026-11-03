from typing import Any

from app.core.supabase import unwrap_response
from app.repositories.base import SupabaseRepository


class SubscriptionRepository(SupabaseRepository):
    table_name = "cleaner_subscriptions"

    def list_recent(self, limit: int = 50, order_by: str = "end_date") -> list[dict[str, Any]]:
        response = (
            self.table()
            .select("id,cleaner_id,plan_id,payment_id,start_date,end_date,status,cleaner_profiles(profiles(first_name,last_name))")
            .order(order_by, desc=True)
            .limit(limit)
            .execute()
        )
        return unwrap_response(response) or []