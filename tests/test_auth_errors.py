from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.exceptions import (
    AuthenticationError,
    AuthenticationRateLimitError,
    AuthenticationUnavailableError,
    PermissionDeniedError,
)
from app.services.auth_service import AuthService


class ProviderError(Exception):
    def __init__(self, message, status, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


@pytest.mark.parametrize("failure,expected,message", [
    (ProviderError("Invalid login credentials", 400, "invalid_credentials"), AuthenticationError, "Invalid email or password."),
    (ProviderError("Invalid login credentials", 400), AuthenticationError, "Invalid email or password."),
    (ProviderError("Email not confirmed", 400, "email_not_confirmed"), AuthenticationError, "Confirm your email address"),
    (ProviderError("User is banned", 403, "user_banned"), PermissionDeniedError, "This account cannot sign in"),
    (ProviderError("Rate limited", 429), AuthenticationRateLimitError, "Too many sign-in attempts"),
    (ProviderError("Rate limited", 400, "over_request_rate_limit"), AuthenticationRateLimitError, "Too many sign-in attempts"),
    (ProviderError("Invalid API key", 401), AuthenticationUnavailableError, "temporarily unavailable"),
    (ProviderError("Database unavailable", 503, "unexpected_failure"), AuthenticationUnavailableError, "temporarily unavailable"),
    (ConnectionError("Connection failed"), AuthenticationUnavailableError, "temporarily unavailable"),
    (TimeoutError("Request timed out"), AuthenticationUnavailableError, "temporarily unavailable"),
    (RuntimeError("Unexpected provider failure"), AuthenticationUnavailableError, "temporarily unavailable"),
])
def test_sign_in_distinguishes_credentials_from_service_failures(failure, expected, message):
    database, authentication = Mock(), Mock()
    authentication.auth.sign_in_with_password.side_effect = failure
    service = AuthService(database, authentication)
    service.admins = Mock()

    with pytest.raises(expected) as error:
        service.sign_in("admin@example.test", "test-password-only")

    assert message in str(error.value)
    service.admins.get_active_admin_by_user_id.assert_not_called()
    database.auth.sign_in_with_password.assert_not_called()


def test_login_strips_email_whitespace_but_preserves_password_exactly():
    database, authentication = Mock(), Mock()
    authentication.auth.sign_in_with_password.return_value = SimpleNamespace(user=None)
    with pytest.raises(AuthenticationUnavailableError):
        AuthService(database, authentication).sign_in("  admin@example.test  ", " test-password-only ")
    authentication.auth.sign_in_with_password.assert_called_once_with({
        "email": "admin@example.test", "password": " test-password-only "
    })


def test_failure_messages_and_logs_do_not_expose_provider_details(caplog):
    authentication = Mock()
    authentication.auth.sign_in_with_password.side_effect = RuntimeError("DO_NOT_EXPOSE_PROVIDER_SECRET")
    with pytest.raises(AuthenticationUnavailableError) as error:
        AuthService(Mock(), authentication).sign_in("admin@example.test", "DO_NOT_EXPOSE_PASSWORD")
    assert "DO_NOT_EXPOSE" not in str(error.value)
    assert "DO_NOT_EXPOSE" not in caplog.text
