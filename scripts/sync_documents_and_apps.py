import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.supabase import get_supabase_admin_client, unwrap_response


def sync():
    c = get_supabase_admin_client()
    cleaners = unwrap_response(c.table("cleaner_profiles").select("id,verification_status").execute()) or []
    apps = unwrap_response(c.table("verification_applications").select("id,cleaner_id,status").execute()) or []
    existing_app_cleaner_ids = {a["cleaner_id"]: a["id"] for a in apps if "cleaner_id" in a}

    for cp in cleaners:
        cid = cp["id"]
        v_stat = cp.get("verification_status") or "PENDING_REVIEW"
        if v_stat not in ["NOT_SUBMITTED", "INCOMPLETE", "PENDING_REVIEW", "VERIFIED", "REJECTED", "SUSPENDED"]:
            v_stat = "PENDING_REVIEW"

        if cid not in existing_app_cleaner_ids:
            print(f"Creating application for cleaner {cid} with status {v_stat}")
            res = c.table("verification_applications").insert({"cleaner_id": cid, "status": v_stat}).execute()
            data = unwrap_response(res) or []
            new_app_id = data[0]["id"] if data else None
            print(f"Created app {new_app_id}")
            if new_app_id:
                existing_app_cleaner_ids[cid] = new_app_id

    # Link any cleaner_verification_documents that have application_id as None
    docs = unwrap_response(c.table("cleaner_verification_documents").select("id,cleaner_id,application_id").is_("application_id", "null").execute()) or []
    print(f"Docs with null application_id: {len(docs)}")
    for d in docs:
        app_id = existing_app_cleaner_ids.get(d["cleaner_id"])
        if app_id:
            c.table("cleaner_verification_documents").update({"application_id": app_id}).eq("id", d["id"]).execute()
            print(f"Linked doc {d['id']} to app {app_id}")

    print("Sync complete.")


if __name__ == "__main__":
    sync()
