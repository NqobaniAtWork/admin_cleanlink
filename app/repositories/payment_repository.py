from typing import Any

from app.core.supabase import unwrap_response
from app.repositories.base import SupabaseRepository


class PaymentRepository(SupabaseRepository):
    table_name = "payments"

    def list_recent(self, limit: int = 50, order_by: str = "created_at") -> list[dict[str, Any]]:
        response = (
            self.table()
            .select("id,booking_id,subscription_id,payer_id,payee_id,amount,currency,payment_method,status,payment_provider,provider_reference,gateway_reference,platform_fee,cleaner_net_amount,authorized_at,paid_at,failed_at,created_at")
            .order(order_by, desc=True)
            .limit(limit)
            .execute()
        )
        rows = unwrap_response(response) or []
        if not rows:
            return []

        # 1. Collect unique profile IDs from payer_id and payee_id
        profile_ids = list({str(r["payer_id"]) for r in rows if r.get("payer_id")} | {str(r["payee_id"]) for r in rows if r.get("payee_id")})

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
                        "name": full_name or prof.get("phone") or "Unknown",
                        "role": prof.get("role") or "",
                        "phone": prof.get("phone") or "",
                    }
            except Exception:
                pass

        # 2. Collect booking information for fallback cleaner/client names
        booking_ids = list({str(r["booking_id"]) for r in rows if r.get("booking_id")})
        booking_cleaner_map: dict[str, str] = {}
        booking_client_map: dict[str, str] = {}
        if booking_ids:
            try:
                b_res = (
                    self.client.table("bookings")
                    .select("id,client_id,cleaner_id,cleaner_profiles(profiles(first_name,last_name)),client_profiles(profiles(first_name,last_name))")
                    .in_("id", booking_ids)
                    .execute()
                )
                for b in unwrap_response(b_res) or []:
                    bid = str(b.get("id"))
                    cl_prof = (b.get("cleaner_profiles") or {}).get("profiles") or {}
                    cl_fn = (cl_prof.get("first_name") or "").strip()
                    cl_ln = (cl_prof.get("last_name") or "").strip()
                    cl_name = f"{cl_fn} {cl_ln}".strip()
                    if cl_name:
                        booking_cleaner_map[bid] = cl_name
                    cp_prof = (b.get("client_profiles") or {}).get("profiles") or {}
                    cp_fn = (cp_prof.get("first_name") or "").strip()
                    cp_ln = (cp_prof.get("last_name") or "").strip()
                    cp_name = f"{cp_fn} {cp_ln}".strip()
                    if cp_name:
                        booking_client_map[bid] = cp_name
            except Exception:
                pass

        # 3. Enrich each row with participant details
        for r in rows:
            payer_id = str(r.get("payer_id") or "")
            payee_id = str(r.get("payee_id") or "")
            booking_id = str(r.get("booking_id") or "")

            payer_info = profile_map.get(payer_id, {})
            payee_info = profile_map.get(payee_id, {})

            payer_name = payer_info.get("name") or booking_client_map.get(booking_id) or (f"User {payer_id[:8]}" if payer_id else "Unknown")
            payee_name = payee_info.get("name") or booking_cleaner_map.get(booking_id)

            if not payee_name:
                if r.get("subscription_id"):
                    payee_name = "CleanLink Platform"
                elif payee_id:
                    payee_name = f"User {payee_id[:8]}"
                else:
                    payee_name = "CleanLink Platform"

            r["payer_name"] = payer_name
            r["payer_role"] = payer_info.get("role") or ("CLIENT" if r.get("booking_id") else ("CLEANER" if r.get("subscription_id") else ""))
            r["payee_name"] = payee_name
            r["payee_role"] = payee_info.get("role") or ("CLEANER" if r.get("booking_id") else ("PLATFORM" if r.get("subscription_id") else ""))

        return rows

    def list_gateways(self) -> list[dict[str, Any]]:
        response = self.client.table("payment_gateways").select("code,name,active,is_primary,capabilities,display_order").order("display_order").execute()
        return unwrap_response(response) or []

    def get_by_id(self, payment_id: str) -> dict[str, Any] | None:
        response = self.table().select("*").eq("id", payment_id).limit(1).execute()
        rows = unwrap_response(response) or []
        return rows[0] if rows else None

    def update_status(self, payment_id: str, status: str, refunded_at: str | None = None) -> None:
        from datetime import datetime, timezone
        payload: dict[str, Any] = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
        if refunded_at is not None:
            payload["refunded_at"] = refunded_at
        self.table().update(payload).eq("id", payment_id).execute()
