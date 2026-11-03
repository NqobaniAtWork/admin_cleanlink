from datetime import datetime, timezone
from typing import Any

from app.core.supabase import unwrap_response
from app.repositories.base import SupabaseRepository


class RefundRepository(SupabaseRepository):
    table_name = "refunds"

    def list_refunds(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = self.table().select(
            "id,payment_id,booking_id,requested_by,amount,reason,status,provider_reference,processed_at,created_at,updated_at"
        )
        if status and status.lower() != "all":
            query = query.eq("status", status.upper())
        response = query.order("created_at", desc=True).limit(limit).execute()
        rows = unwrap_response(response) or []
        if not rows:
            return []

        # 1. Collect profile IDs for requesters
        profile_ids = list({str(r["requested_by"]) for r in rows if r.get("requested_by")})
        profile_map: dict[str, dict[str, Any]] = {}
        if profile_ids:
            try:
                p_res = self.client.table("profiles").select("id,first_name,last_name,role,phone").in_("id", profile_ids).execute()
                for prof in unwrap_response(p_res) or []:
                    pid = str(prof.get("id"))
                    fn = (prof.get("first_name") or "").strip()
                    ln = (prof.get("last_name") or "").strip()
                    full_name = f"{fn} {ln}".strip()
                    profile_map[pid] = {
                        "name": full_name or prof.get("phone") or "Unknown User",
                        "role": prof.get("role") or "",
                        "phone": prof.get("phone") or "",
                    }
            except Exception:
                pass

        # 2. Collect payment details
        payment_ids = list({str(r["payment_id"]) for r in rows if r.get("payment_id")})
        payment_map: dict[str, dict[str, Any]] = {}
        if payment_ids:
            try:
                pay_res = self.client.table("payments").select("id,amount,currency,payment_method,payment_provider,status").in_("id", payment_ids).execute()
                for pay in unwrap_response(pay_res) or []:
                    payment_map[str(pay.get("id"))] = pay
            except Exception:
                pass

        for r in rows:
            req_id = str(r.get("requested_by") or "")
            p_info = profile_map.get(req_id, {})
            r["requester_name"] = p_info.get("name") or (f"User {req_id[:8]}" if req_id else "System")
            r["requester_role"] = p_info.get("role") or "CLIENT"

            pay_id = str(r.get("payment_id") or "")
            pay_data = payment_map.get(pay_id, {})
            r["payment_total"] = pay_data.get("amount")
            r["payment_currency"] = pay_data.get("currency") or "ZAR"
            r["payment_method"] = pay_data.get("payment_method")
            r["payment_provider"] = pay_data.get("payment_provider") or "Cash"
            r["payment_current_status"] = pay_data.get("status")

        return rows

    def get_by_id(self, refund_id: str) -> dict[str, Any] | None:
        response = self.table().select("*").eq("id", refund_id).limit(1).execute()
        rows = unwrap_response(response) or []
        return rows[0] if rows else None

    def get_refunds_for_payment(self, payment_id: str) -> list[dict[str, Any]]:
        response = self.table().select("id,amount,status,created_at").eq("payment_id", payment_id).execute()
        return unwrap_response(response) or []

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        response = self.table().insert(data).execute()
        rows = unwrap_response(response) or []
        return rows[0] if rows else data

    def update_status(self, refund_id: str, status: str, processed_at: str | None = None) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {"status": status, "updated_at": now_iso}
        if processed_at:
            payload["processed_at"] = processed_at
        self.table().update(payload).eq("id", refund_id).execute()
