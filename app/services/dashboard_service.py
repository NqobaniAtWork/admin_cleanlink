from typing import Any


class DashboardService:
    """Builds the admin dashboard from live Supabase tables/views."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def _count(self, table: str, **filters: Any) -> int:
        query = self.client.table(table).select("id", count="exact").limit(1)
        for column, value in filters.items():
            query = query.eq(column, value)
        response = query.execute()
        return int(getattr(response, "count", 0) or 0)

    def _count_in(self, table: str, column: str, values: list[str]) -> int:
        response = self.client.table(table).select("id", count="exact").in_(column, values).limit(1).execute()
        return int(getattr(response, "count", 0) or 0)

    def metrics(self) -> list[dict[str, str | int]]:
        return [
            {"label": "Pending Cleaner Verifications", "value": self._count("verification_applications", status="PENDING_REVIEW")},
            {"label": "Active Cleaners", "value": self._count("cleaner_profiles", verification_status="VERIFIED", subscription_status="ACTIVE")},
            {"label": "Expired Subscriptions", "value": self._count("cleaner_subscriptions", status="EXPIRED")},
            {"label": "Active Jobs", "value": self._count_in("bookings", "status", ["ACCEPTED", "CONFIRMED", "IN_PROGRESS"])},
            {"label": "Pending Refunds", "value": self._count("refunds", status="REQUESTED")},
            {"label": "Suspended Accounts", "value": self._count("profiles", account_status="SUSPENDED")},
        ]