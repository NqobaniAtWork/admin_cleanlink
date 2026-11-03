from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import dependencies
from app.models.enums import AdminRole
from app.schemas.auth_schema import AdminSession
from app.services.auth_service import AuthService


def request_with_admin():
    identity = AdminSession(user_id="admin-1", email="admin@example.test", role=AdminRole.SUPER_ADMIN)
    return Request({"type": "http", "session": {"admin": identity.model_dump(mode="json")}})


def authorize(monkeypatch, row=None, failure=None):
    client = Mock()
    query = client.table.return_value.select.return_value.eq.return_value.limit.return_value
    query.execute.side_effect = failure
    query.execute.return_value = SimpleNamespace(data=[row] if row else [])
    monkeypatch.setattr(dependencies, "get_supabase_admin_client", lambda: client)


def profile(**overrides):
    return {"id": "admin-profile-1", "user_id": "admin-1", "active": True,
            "admin_role": "SUPPORT_ADMIN", "profiles": {"account_status": "ACTIVE", "first_name": "Test"}, **overrides}


def test_cookie_super_admin_role_is_replaced_by_current_database_role(monkeypatch):
    authorize(monkeypatch, profile())
    request = request_with_admin()
    assert dependencies.current_admin(request).role == AdminRole.SUPPORT_ADMIN
    assert request.session["admin"]["role"] == "SUPPORT_ADMIN"


@pytest.mark.parametrize("row", [None, profile(active=False), profile(profiles={"account_status": "SUSPENDED"}), profile(profiles={})])
def test_removed_disabled_or_unverified_admin_loses_access(monkeypatch, row):
    authorize(monkeypatch, row)
    request = request_with_admin()
    assert dependencies.current_admin(request) is None
    assert request.session == {}


def test_database_outage_does_not_authorize_using_cookie_role(monkeypatch):
    authorize(monkeypatch, failure=RuntimeError("database unavailable"))
    with pytest.raises(HTTPException) as error:
        dependencies.current_admin(request_with_admin())
    assert error.value.status_code == 503


def test_login_uses_separate_auth_client_and_service_client_for_role_lookup(monkeypatch):
    database, authentication = Mock(), Mock()
    authentication.auth.sign_in_with_password.return_value = SimpleNamespace(user=SimpleNamespace(id="admin-1", email="admin@example.test"))
    service = AuthService(database, authentication)
    service.admins = Mock()
    service.admins.get_active_admin_by_user_id.return_value = profile()
    assert service.sign_in("admin@example.test", "unit-test-only").role == AdminRole.SUPPORT_ADMIN
    authentication.auth.sign_in_with_password.assert_called_once()
    database.auth.sign_in_with_password.assert_not_called()
    service.sign_out()
    authentication.auth.sign_out.assert_called_once()
    database.auth.sign_out.assert_not_called()


def test_each_auth_client_is_fresh_and_does_not_persist_sessions(monkeypatch):
    from app.core import supabase
    factory = Mock(side_effect=lambda *args, **kwargs: object())
    monkeypatch.setattr(supabase, "create_client", factory)
    assert supabase.get_supabase_auth_client() is not supabase.get_supabase_auth_client()
    options = factory.call_args.kwargs["options"]
    assert options.persist_session is False
    assert options.auto_refresh_token is False


@pytest.mark.parametrize("account_status", ["SUSPENDED", "DELETED"])
def test_login_rejects_an_inactive_underlying_account(account_status):
    from app.exceptions import PermissionDeniedError
    database, authentication = Mock(), Mock()
    authentication.auth.sign_in_with_password.return_value = SimpleNamespace(user=SimpleNamespace(id="admin-1", email="admin@example.test"))
    service = AuthService(database, authentication)
    service.admins = Mock()
    service.admins.get_active_admin_by_user_id.return_value = profile(profiles={"account_status": account_status})
    with pytest.raises(PermissionDeniedError):
        service.sign_in("admin@example.test", "unit-test-only")
    authentication.auth.sign_out.assert_called_once()
    database.auth.sign_out.assert_not_called()


@pytest.mark.parametrize("grants,expected", [(["finance:view"], False), (["identity_verification.view"], True), ([], False)])
def test_current_database_grants_replace_stale_cookie_permissions(monkeypatch, grants, expected):
    authorize(monkeypatch, profile(custom_permissions=grants))
    request = request_with_admin()
    request.session["admin"]["custom_permissions"] = ["identity_verification.view", "settings:manage"]
    current = dependencies.current_admin(request)
    assert current.has_permission("identity_verification.view") is expected
    assert not current.has_permission("settings:manage")
    assert request.session["admin"]["custom_permissions"] == grants


def test_permission_lookup_outage_cannot_retain_cookie_grants(monkeypatch):
    client = Mock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=[profile(custom_permissions=None)])
    client.auth.admin.get_user_by_id.side_effect = RuntimeError("synthetic outage")
    monkeypatch.setattr(dependencies, "get_supabase_admin_client", lambda: client)
    with pytest.raises(HTTPException) as error:
        dependencies.current_admin(request_with_admin())
    assert error.value.status_code == 503


def test_missing_database_permission_column_uses_current_auth_app_metadata(monkeypatch):
    client = Mock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=[profile()])
    client.auth.admin.get_user_by_id.return_value = SimpleNamespace(user=SimpleNamespace(app_metadata={"permissions": ["finance:view"]}))
    monkeypatch.setattr(dependencies, "get_supabase_admin_client", lambda: client)
    current = dependencies.current_admin(request_with_admin())
    assert current.has_permission("finance:view")
    assert not current.has_permission("settings:manage")


def test_shared_database_and_login_client_is_rejected_before_authentication():
    shared = Mock()
    with pytest.raises(ValueError, match="separate instances"):
        AuthService(shared, shared)
    shared.auth.sign_in_with_password.assert_not_called()
