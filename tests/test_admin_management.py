from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.models.enums import AdminRole
from app.schemas.auth_schema import AdminCreate
from app.services.admin_management_service import AdminManagementService


class Query:
    def __init__(self, client, table: str) -> None:
        self.client = client
        self.table = table
        self.row = None

    def insert(self, row):
        self.row = row
        return self

    def execute(self):
        self.client.inserts.append((self.table, self.row))
        if self.table == "admin_profiles":
            return SimpleNamespace(data=[{"id": "admin-profile-id", **self.row}])
        return SimpleNamespace(data=[self.row])


class AdminAuth:
    def __init__(self, client) -> None:
        self.client = client

    def create_user(self, attributes):
        self.client.auth_attributes = attributes
        return SimpleNamespace(user=SimpleNamespace(id="auth-user-id"))

    def delete_user(self, user_id):
        self.client.deleted.append(user_id)


class FakeClient:
    def __init__(self) -> None:
        self.inserts = []
        self.deleted = []
        self.updates = []
        self.auth_attributes = None
        self.auth = SimpleNamespace(admin=AdminAuth(self))

    def table(self, name: str):
        return Query(self, name)


def test_super_admin_creates_auth_profile_role_and_audit_action() -> None:
    client = FakeClient()
    payload = AdminCreate(
        email="admin@example.com",
        password="a-strong-temporary-password",
        first_name="CleanLink",
        last_name="Admin",
        role=AdminRole.FINANCE_ADMIN,
    )

    created = AdminManagementService(client).create_admin("actor-id", payload)

    assert created["admin_role"] == "FINANCE_ADMIN"
    assert client.auth_attributes["app_metadata"]["admin_role"] == "FINANCE_ADMIN"
    assert "payments:review" in client.auth_attributes["app_metadata"]["permissions"]
    assert client.auth_attributes["user_metadata"]["role"] == "ADMIN"
    assert ("profiles", {"id": "auth-user-id", "role": "ADMIN", "first_name": "CleanLink", "last_name": "Admin", "account_status": "ACTIVE"}) in client.inserts
    assert any(table == "admin_actions" and row["action"] == "ADMIN_CREATED" for table, row in client.inserts)
    assert client.deleted == []


def test_create_admin_with_custom_privileges() -> None:
    client = FakeClient()
    payload = AdminCreate(
        email="custom@example.com",
        password="a-strong-temporary-password",
        first_name="Custom",
        last_name="Operator",
        role=AdminRole.SUPPORT_ADMIN,
        permissions=["verification:review", "refunds:manage"],
    )

    created = AdminManagementService(client).create_admin("super-admin-id", payload)

    assert created["admin_role"] == "SUPPORT_ADMIN"
    assert client.auth_attributes["app_metadata"]["permissions"] == ["verification:review", "refunds:manage"]
    assert any(
        table == "admin_profiles" and row.get("custom_permissions") == ["verification:review", "refunds:manage"]
        for table, row in client.inserts
    )
    assert any(table == "admin_actions" and row["action"] == "ADMIN_CREATED" for table, row in client.inserts)


def test_update_admin_permissions() -> None:
    client = FakeClient()

    class MockQuery(Query):
        def __init__(self, client, table):
            super().__init__(client, table)
            self._select_col = None

        def select(self, col):
            self._select_col = col
            return self

        def eq(self, col, val):
            return self

        def update(self, payload):
            self.client.updates.append((self.table, payload))
            return self

        def execute(self):
            if self.row is not None:
                self.client.inserts.append((self.table, self.row))
            if self.table == "admin_profiles" and self._select_col:
                return SimpleNamespace(data=[{"id": "admin-123", "user_id": "user-456", "admin_role": "SUPPORT_ADMIN"}])
            return SimpleNamespace(data=[{"id": "admin-123"}])

    client.table = lambda name: MockQuery(client, name)
    client.auth.admin.get_user_by_id = lambda uid: SimpleNamespace(app_metadata={})
    client.auth.admin.update_user_by_id = lambda uid, payload: None

    service = AdminManagementService(client)
    res = service.update_admin_permissions(
        actor_user_id="super-admin-id",
        admin_id="admin-123",
        permissions=["cleaners:manage", "refunds:manage"],
    )
    assert res is not None
    assert any(
        t == "admin_profiles" and u.get("custom_permissions") == ["cleaners:manage", "refunds:manage"]
        for t, u in client.updates
    )
    assert any(t == "admin_actions" and r.get("action") == "ADMIN_PERMISSIONS_UPDATED" for t, r in client.inserts)


def test_update_personal_details() -> None:
    client = FakeClient()

    class MockQuery(Query):
        def update(self, payload):
            self.client.updates.append((self.table, payload))
            return self

        def eq(self, col, val):
            return self

        def execute(self):
            if self.row is not None:
                self.client.inserts.append((self.table, self.row))
            return SimpleNamespace(data=[{"id": "user-123", "first_name": "NewFirst", "last_name": "NewLast"}])

    client.table = lambda name: MockQuery(client, name)
    client.auth.admin.update_user_by_id = lambda uid, attrs: None

    service = AdminManagementService(client)
    res = service.update_personal_details("user-123", "NewFirst", "NewLast", "+27 82 999 8888")
    assert res is not None
    assert any(t == "profiles" and u.get("first_name") == "NewFirst" for t, u in client.updates)
    assert any(t == "admin_actions" and r.get("action") == "PROFILE_UPDATED" for t, r in client.inserts)