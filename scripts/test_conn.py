import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.core.supabase import get_supabase_admin_client


def main():
    settings = get_settings()
    print("--------------------------------------------------")
    print(f"Connecting to Supabase: {settings.supabase_url}")
    print(f"Session Secret configured: {'Yes' if settings.session_secret else 'No'}")
    print("--------------------------------------------------")

    client = get_supabase_admin_client()

    # 1. Test Auth API
    try:
        users = client.auth.admin.list_users(page=1, per_page=10)
        user_list = users if isinstance(users, list) else getattr(users, "users", [])
        print(f"[OK] Auth API reachable ({len(user_list)} auth users found)")
    except Exception as exc:
        print(f"[FAIL] Auth API error: {exc}")

    # 2. Test Database Tables
    tables_to_check = [
        ("profiles", "id"),
        ("cleaner_profiles", "id"),
        ("client_profiles", "id"),
        ("verification_applications", "id"),
        ("cleaner_verification_documents", "id"),
        ("verification_document_types", "id"),
        ("cleaner_subscriptions", "id"),
        ("bookings", "id"),
        ("payments", "id"),
        ("refunds", "id"),
        ("system_settings", "key"),
        ("admin_profiles", "id"),
        ("admin_actions", "id"),
        ("audit_logs", "id"),
    ]

    print("\nChecking database tables:")
    for table_name, select_col in tables_to_check:
        try:
            res = client.table(table_name).select(select_col, count="exact").limit(1).execute()
            count = getattr(res, "count", None)
            print(f"  [OK] public.{table_name:<26} (count: {count if count is not None else len(res.data or [])})")
        except Exception as exc:
            msg = str(exc)
            if "permission denied" in msg.lower():
                print(f"  [LOCKED] public.{table_name:<26} (permission denied: grant needed)")
            else:
                print(f"  [ERROR]  public.{table_name:<26} ({exc})")

    # Document details
    try:
        dt = client.table("verification_document_types").select("*").execute()
        print("\nVerification Document Types:", dt.data)
    except Exception as e:
        print("Doc types error:", e)

    try:
        cvd = client.table("cleaner_verification_documents").select("*").execute()
        print("Cleaner Verification Documents:", cvd.data)
    except Exception as e:
        print("Cleaner docs error:", e)

    try:
        buckets = client.storage.list_buckets()
        print("Storage Buckets:", [b.name for b in buckets])
        for b in buckets:
            try:
                files = client.storage.from_(b.name).list()
                print(f"  Bucket '{b.name}' files:", files)
            except Exception as fe:
                print(f"  Bucket '{b.name}' list error:", fe)
    except Exception as e:
        print("Storage buckets error:", e)

    # 3. Test App Services
    print("\nTesting Admin Services & Queries:")
    from app.services.cleaner_service import CleanerService
    from app.services.verification_service import VerificationService
    from app.services.user_audit_service import UserAuditService
    from app.services.dashboard_service import DashboardService
    from app.repositories.settings_repository import SettingsRepository

    try:
        cleaners = CleanerService(client).list_cleaners()
        print(f"  [OK] CleanerService.list_cleaners() -> {len(cleaners)} cleaners returned")
        for c in cleaners:
            p = c.get("profiles") or {}
            print(f"       - {p.get('first_name', '')} {p.get('last_name', '')} (Status: {c.get('verification_status')}, Rating: {c.get('overall_rating')})")
    except Exception as exc:
        print(f"  [FAIL] CleanerService: {exc}")

    try:
        pending = VerificationService(client).pending_applications()
        print(f"  [OK] VerificationService.pending_applications() -> {len(pending)} applications returned")
        for app_row in pending:
            cp = app_row.get("cleaner_profiles") or {}
            p = cp.get("profiles") or {}
            print(f"       - App ID: {app_row.get('id')} | Cleaner: {p.get('first_name', '')} {p.get('last_name', '')} (Status: {app_row.get('status')})")
    except Exception as exc:
        print(f"  [FAIL] VerificationService: {exc}")

    try:
        user_res = UserAuditService(client).search_users(page=1)
        print(f"  [OK] UserAuditService.search_users() -> {len(user_res.get('rows', []))} users (total count: {user_res.get('count')})")
        for u in user_res.get("rows", []):
            print(f"       - {u.get('first_name', '')} {u.get('last_name', '')} <{u.get('email')}> | Role: {u.get('role')} | Verification: {u.get('verification_status')}")
    except Exception as exc:
        print(f"  [FAIL] UserAuditService: {exc}")

    try:
        metrics = DashboardService(client).metrics()
        print(f"  [OK] DashboardService.metrics() -> {metrics}")
    except Exception as exc:
        print(f"  [FAIL] DashboardService: {exc}")

    try:
        settings_rows = SettingsRepository(client).list_settings()
        print(f"  [OK] SettingsRepository.list_settings() -> {len(settings_rows)} settings loaded")
    except Exception as exc:
        print(f"  [FAIL] SettingsRepository: {exc}")

    try:
        activity_res = UserAuditService(client).audit_logs(page=1)
        print(f"  [OK] UserAuditService.audit_logs() -> {len(activity_res.get('rows', []))} events")
    except Exception as exc:
        print(f"  [FAIL] UserAuditService activity: {exc}")

    try:
        from app.services.subscription_service import SubscriptionService
        from app.repositories.subscription_repository import SubscriptionRepository
        sub_srv = SubscriptionService(client)
        plans = sub_srv.list_plans()
        recent_subs = SubscriptionRepository(client).list_recent()
        print(f"  [OK] Subscriptions: {len(plans)} plans available, {len(recent_subs)} active cleaner subscriptions found")
    except Exception as exc:
        print(f"  [FAIL] Subscriptions check: {exc}")

    try:
        from app.services.admin_management_service import AdminManagementService
        admins_list = AdminManagementService(client).list_admins()
        print(f"  [OK] AdminManagementService.list_admins() -> {len(admins_list)} administrators found")
        for a in admins_list:
            prof = a.get("profiles") or {}
            print(f"       - {prof.get('first_name', '')} {prof.get('last_name', '')} <{a.get('email', '')}> | Level: {a.get('admin_role')} | Active: {a.get('active')}")
    except Exception as exc:
        print(f"  [FAIL] AdminManagementService: {exc}")

    print("\n--------------------------------------------------")


if __name__ == "__main__":
    main()
