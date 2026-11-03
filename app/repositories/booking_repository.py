from typing import Any

from app.repositories.base import SupabaseRepository


class BookingRepository(SupabaseRepository):
    table_name = "bookings"

    def active_count(self) -> int:
        return self.count(status="IN_PROGRESS")

    def requested_count(self) -> int:
        return self.count(status="REQUESTED")