import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.exceptions import AdminManagementError
from app.services.admin_management_service import reset_admin_password


@pytest.fixture
def client():
    database = Mock()
    database.auth.admin.list_users.return_value = [SimpleNamespace(
        id="admin-1", email="admin@example.test", email_confirmed_at="2026-10-01T00:00:00Z"
    )]
    database.auth.admin.update_user_by_id.return_value = SimpleNamespace(user=SimpleNamespace(id="admin-1"))
    rows = {
        "profiles": [{"role": "ADMIN", "account_status": "ACTIVE"}],
        "admin_profiles": [{"admin_role": "SUPER_ADMIN", "active": True}],
    }
    queries = {name: Mock() for name in rows}
    for name, query in queries.items():
        query.select.return_value.eq.return_value.limit.return_value.execute.side_effect = lambda name=name: SimpleNamespace(data=rows[name])
    database.table.side_effect = lambda name: queries[name]
    database.rows = rows
    return database


def test_reset_changes_only_the_confirmed_admin_password(client):
    password = " private-test-password "
    reset_admin_password(client, " ADMIN@example.test ", password)
    client.auth.admin.update_user_by_id.assert_called_once_with("admin-1", {"password": password})
    client.auth.admin.create_user.assert_not_called()
    client.auth.admin.delete_user.assert_not_called()


@pytest.mark.parametrize("role", ["CLIENT", "CLEANER"])
def test_reset_never_changes_a_client_or_cleaner_password(client, role):
    client.rows["profiles"][0]["role"] = role
    with pytest.raises(AdminManagementError, match="not an active administrator"):
        reset_admin_password(client, "admin@example.test", "private-test-password")
    client.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.parametrize("table,rows", [
    ("profiles", []),
    ("profiles", [{"role": "ADMIN", "account_status": "SUSPENDED"}]),
    ("admin_profiles", []),
    ("admin_profiles", [{"admin_role": "SUPER_ADMIN", "active": False}]),
    ("admin_profiles", [{"admin_role": "UNKNOWN_ROLE", "active": True}]),
])
def test_reset_does_not_restore_disabled_accounts_or_grant_roles(client, table, rows):
    client.rows[table] = rows
    with pytest.raises(AdminManagementError):
        reset_admin_password(client, "admin@example.test", "private-test-password")
    client.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.parametrize("users", [[], [SimpleNamespace(id="admin-1", email="admin@example.test", email_confirmed_at=None)]])
def test_reset_rejects_missing_or_unconfirmed_auth_accounts(client, users):
    client.auth.admin.list_users.return_value = users
    with pytest.raises(AdminManagementError, match="No confirmed administrator"):
        reset_admin_password(client, "admin@example.test", "private-test-password")
    client.auth.admin.update_user_by_id.assert_not_called()


def test_reset_stops_if_database_verification_fails(client):
    client.table.side_effect = RuntimeError("DO_NOT_EXPOSE_SERVER_SECRET")
    with pytest.raises(AdminManagementError, match="could not be checked") as error:
        reset_admin_password(client, "admin@example.test", "private-test-password")
    assert "DO_NOT_EXPOSE" not in str(error.value)
    client.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.parametrize("password", ["too-short", " " * 12])
def test_reset_rejects_short_or_blank_passwords_before_network_requests(client, password):
    with pytest.raises(AdminManagementError, match="at least 12 characters"):
        reset_admin_password(client, "admin@example.test", password)
    client.auth.admin.list_users.assert_not_called()
    client.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.parametrize("response", [SimpleNamespace(user=None), SimpleNamespace(user=SimpleNamespace(id="different-user"))])
def test_reset_requires_confirmation_of_the_correct_auth_user(client, response):
    client.auth.admin.update_user_by_id.return_value = response
    with pytest.raises(AdminManagementError, match="could not be confirmed"):
        reset_admin_password(client, "admin@example.test", "private-test-password")


@pytest.fixture
def command():
    path = Path(__file__).resolve().parents[1] / "scripts" / "reset_admin_password.py"
    spec = importlib.util.spec_from_file_location("reset_admin_password_command", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_terminal(monkeypatch, command, answers=("private-test-password", "private-test-password")):
    monkeypatch.setattr(command.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _: "y")
    passwords = iter(answers)
    monkeypatch.setattr(command, "getpass", lambda _: next(passwords))
    client_factory = Mock()
    updater = Mock()
    monkeypatch.setattr(command, "get_supabase_admin_client", client_factory)
    monkeypatch.setattr(command, "reset_admin_password", updater)
    return client_factory, updater


def test_command_uses_private_prompts_without_printing_the_password(command, monkeypatch, capsys):
    factory, updater = prepare_terminal(monkeypatch, command)
    command.main(["--email", "admin@example.com"])
    updater.assert_called_once_with(factory.return_value, "admin@example.com", "private-test-password")
    output = capsys.readouterr().out
    assert "Admin password updated" in output
    assert "private-test-password" not in output
    assert "sessions are not revoked" in output


def test_command_does_not_update_without_explicit_confirmation(command, monkeypatch):
    factory, updater = prepare_terminal(monkeypatch, command)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    command.main(["--email", "admin@example.com"])
    factory.assert_not_called()
    updater.assert_not_called()


def test_command_rejects_noninteractive_input(command, monkeypatch):
    factory, updater = prepare_terminal(monkeypatch, command)
    monkeypatch.setattr(command.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(SystemExit, match="interactive terminal"):
        command.main(["--email", "admin@example.com"])
    factory.assert_not_called()
    updater.assert_not_called()


def test_command_rejects_mismatched_passwords(command, monkeypatch):
    factory, updater = prepare_terminal(monkeypatch, command, ("private-test-password", "different-test-password"))
    with pytest.raises(SystemExit, match="Passwords do not match"):
        command.main(["--email", "admin@example.com"])
    factory.assert_not_called()
    updater.assert_not_called()


def test_command_rejects_an_invalid_email_without_contacting_the_server(command, monkeypatch):
    factory, updater = prepare_terminal(monkeypatch, command)
    with pytest.raises(SystemExit, match="valid administrator email"):
        command.main(["--email", "not-an-email"])
    factory.assert_not_called()
    updater.assert_not_called()


def test_command_stops_if_the_password_cannot_be_hidden(command, monkeypatch):
    factory, updater = prepare_terminal(monkeypatch, command)

    def unsafe_prompt(_):
        raise command.GetPassWarning("Echo cannot be disabled")

    monkeypatch.setattr(command, "getpass", unsafe_prompt)
    with pytest.raises(SystemExit, match="private password prompt"):
        command.main(["--email", "admin@example.com"])
    factory.assert_not_called()
    updater.assert_not_called()


def test_provider_failure_does_not_expose_passwords_or_credentials(client):
    client.auth.admin.update_user_by_id.side_effect = RuntimeError("DO_NOT_EXPOSE_SERVER_SECRET")
    with pytest.raises(AdminManagementError, match="could not be confirmed") as error:
        reset_admin_password(client, "admin@example.test", "DO_NOT_EXPOSE_PASSWORD")
    assert "DO_NOT_EXPOSE" not in str(error.value)
