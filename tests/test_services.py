from datetime import date
from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.models.enums import AdminRole
from app.schemas.admin_actions import AdminActionCreate
from app.services.cleaner_service import CleanerService
from app.services.verification_service import VerificationService
from app.services.subscription_service import SubscriptionService
from app.services.user_audit_service import UserAuditService
from app.security.roles import has_permission


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.data = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        return self

    def in_(self, col, vals):
        return self

    def ilike(self, col, val):
        return self

    def or_(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, count):
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    def update(self, fields):
        self.client.updates.append((self.table_name, fields))
        return self

    def insert(self, row):
        self.client.inserts.append((self.table_name, row))
        return self

    def execute(self):
        if self.table_name == "admin_profiles":
            return SimpleNamespace(data=[{"id": "test-admin-prof-id", "user_id": "admin-1", "admin_role": "SUPER_ADMIN", "active": True}])
        if self.table_name == "cleaner_subscriptions":
            return SimpleNamespace(data=[{"id": "sub-id"}])
        if self.table_name == "audit_logs":
            return SimpleNamespace(data=[{
                "id": "audit-1",
                "actor_id": "admin-1",
                "actor_role": "ADMIN",
                "action": "CLEANER_VERIFIED",
                "target_type": "cleaner_profiles",
                "target_id": "cp-1",
                "reason": "All documents verified",
                "new_state": None,
                "metadata": {},
                "created_at": "2026-10-19T10:00:00Z"
            }])
        if self.table_name == "admin_actions":
            return SimpleNamespace(data=[{
                "id": "aa-2",
                "admin_user_id": "admin-1",
                "action": "USER_ACTIVATED",
                "target_type": "profiles",
                "target_id": "user-2",
                "reason": "Administrative reactivation",
                "metadata": {},
                "created_at": "2026-10-18T12:00:00Z"
            }])
        if self.table_name == "profiles":
            return SimpleNamespace(data=[
                {"id": "admin-1", "first_name": "Nqobani", "last_name": "Gumede", "role": "ADMIN", "phone": "0821234567"},
                {"id": "user-2", "first_name": "Zonke", "last_name": "Cele", "role": "CLEANER", "phone": "0829876543"},
            ])
        if self.table_name == "cleaner_profiles":
            return SimpleNamespace(data=[{"id": "cp-1", "user_id": "user-2"}])
        if self.table_name == "client_profiles":
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[{"id": "rec-id"}], count=1)


class MockAuthAdmin:
    def list_users(self, page=1, per_page=1000):
        return [
            SimpleNamespace(id="admin-1", email="nqobani@cleanlink.co.za", user_metadata={"fullName": "Nqobani Gumede"}),
            SimpleNamespace(id="user-2", email="celezonke0@gmail.com", user_metadata={"fullName": "Zonke Cele"}),
        ]


class MockAuth:
    def __init__(self):
        self.admin = MockAuthAdmin()


class MockClient:
    def __init__(self):
        self.inserts = []
        self.updates = []
        self.auth = MockAuth()

    def table(self, table_name: str):
        return FakeQuery(self, table_name)


def test_verification_service_approves_cleaner_and_records_audit():
    client = MockClient()
    service = VerificationService(client)
    service.approve_cleaner("admin-user-1", "admin-prof-1", "app-123", "cleaner-456", "All docs valid")

    assert any(t == "verification_applications" and u.get("status") == "VERIFIED" for t, u in client.updates)
    assert any(t == "cleaner_profiles" and u.get("verification_status") == "VERIFIED" for t, u in client.updates)
    assert any(t == "admin_actions" and r.get("action") == "CLEANER_VERIFIED" for t, r in client.inserts)


def test_verification_service_rejects_cleaner_and_records_audit():
    client = MockClient()
    service = VerificationService(client)
    service.reject_cleaner("admin-user-1", "admin-prof-1", "app-123", "cleaner-456", "Blurry document")

    assert any(t == "verification_applications" and u.get("status") == "REJECTED" for t, u in client.updates)
    assert any(t == "cleaner_profiles" and u.get("verification_status") == "REJECTED" for t, u in client.updates)
    assert any(t == "admin_actions" and r.get("action") == "CLEANER_REJECTED" for t, r in client.inserts)


def test_cleaner_service_suspend_and_activate():
    client = MockClient()
    service = CleanerService(client)

    service.suspend_cleaner("admin-user-1", "cleaner-456", "Complaint received")
    assert any(t == "cleaner_profiles" and u.get("verification_status") == "SUSPENDED" for t, u in client.updates)
    assert any(t == "admin_actions" and r.get("action") == "ACCOUNT_SUSPENDED" for t, r in client.inserts)

    service.activate_cleaner("admin-user-1", "cleaner-456", "Resolved")
    assert any(t == "cleaner_profiles" and u.get("verification_status") == "VERIFIED" for t, u in client.updates)
    assert any(t == "admin_actions" and r.get("action") == "ACCOUNT_ACTIVATED" for t, r in client.inserts)


def test_subscription_service_activates_cleaner_profile():
    client = MockClient()
    service = SubscriptionService(client)

    res = service.activate_subscription(
        admin_user_id="admin-user-1",
        cleaner_id="cleaner-456",
        plan_id="plan-annual",
        payment_id="pay-999",
        payment_date=date(2026, 5, 10),
        reason="Manual bank payment verified",
    )
    assert res is not None
    assert any(t == "cleaner_subscriptions" and r.get("cleaner_id") == "cleaner-456" for t, r in client.inserts)
    assert any(t == "cleaner_profiles" and u.get("subscription_status") == "ACTIVE" for t, u in client.updates)
    assert any(t == "admin_actions" and r.get("action") == "SUBSCRIPTION_ACTIVATED" for t, r in client.inserts)


def test_verification_service_reviews_document():
    client = MockClient()
    service = VerificationService(client)
    service.review_document("admin-user-1", "admin-prof-1", "doc-123", "cleaner-456", "APPROVED", "Document legible")

    assert any(t == "cleaner_verification_documents" and u.get("status") == "APPROVED" for t, u in client.updates)
    assert any(t == "admin_actions" and r.get("action") == "DOCUMENT_APPROVED" for t, r in client.inserts)


def test_super_admin_has_all_permissions():
    for perm in ["users:view", "cleaners:view", "cleaners:manage", "verification:review", "subscriptions:view", "subscriptions:manage", "bookings:view", "bookings:manage", "finance:view", "finance:manage", "settings:view", "settings:manage", "admins:manage", "audit:view"]:
        assert has_permission(AdminRole.SUPER_ADMIN, perm)


def test_user_audit_service_enriches_admin_name_and_target_name():
    client = MockClient()
    service = UserAuditService(client)
    res = service.audit_logs()
    rows = res["rows"]
    assert len(rows) >= 2

    # Check row 1 (from audit_logs)
    r1 = next(r for r in rows if r["action"] == "CLEANER_VERIFIED")
    assert r1["actor_name"] == "Nqobani Gumede"
    assert r1["actor_email"] == "nqobani@cleanlink.co.za"
    assert r1["actor_role_label"] == "SUPER_ADMIN"
    assert r1["target_name"] == "Zonke Cele"
    assert r1["display_reason"] == "All documents verified"

    # Check row 2 (from admin_actions merged in)
    r2 = next(r for r in rows if r["action"] == "USER_ACTIVATED")
    assert r2["actor_name"] == "Nqobani Gumede"
    assert r2["target_name"] == "Zonke Cele"
    assert r2["display_reason"] == "Administrative reactivation"
