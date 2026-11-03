import os
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("CLEANLINK_ADMIN_SESSION_SECRET", "test-session-secret-with-at-least-32-characters")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SECRET_KEY", "mock-supabase-secret-key-fixture")

from app.dependencies import ensure_permission
from app.main import app
from app.api import auth
from app.exceptions import AuthenticationError, AuthenticationRateLimitError, AuthenticationUnavailableError, PermissionDeniedError
from app.models.enums import AdminRole
from app.schemas.auth_schema import AdminSession


def test_login_page_renders() -> None:
    client = TestClient(app)
    response = client.get("/login")

    assert response.status_code == 200
    assert "CleanLink" in response.text
    assert "/static/cleanlink_logo.png" in response.text

    logo_resp = client.get("/static/cleanlink_logo.png")
    assert logo_resp.status_code == 200
    assert logo_resp.headers["content-type"] in ("image/png", "image/x-png")


def test_root_redirects_to_login_for_anonymous_admin() -> None:
    response = TestClient(app).get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admins_page_redirects_unauthenticated() -> None:
    response = TestClient(app).get("/admins", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admins_manage_forbidden_for_non_super_admin() -> None:
    support_admin = AdminSession(
        user_id="support-user",
        email="support@cleanlink.co.za",
        role=AdminRole.SUPPORT_ADMIN,
        custom_permissions=["verification:review", "cleaners:manage"],
    )
    with pytest.raises(HTTPException) as exc_info:
        ensure_permission(support_admin, "admins:manage")
    assert exc_info.value.status_code == 403


def test_super_admin_passes_admins_manage() -> None:
    super_admin = AdminSession(
        user_id="super-user",
        email="super@cleanlink.co.za",
        role=AdminRole.SUPER_ADMIN,
    )
    # Should not raise exception
    ensure_permission(super_admin, "admins:manage")


def test_admins_template_renders_without_jinja_error(monkeypatch) -> None:
    from starlette.requests import Request
    from app.api.admins import _render
    from app.api import admins
    service = Mock()
    service.list_admins.return_value = []
    monkeypatch.setattr(admins, "AdminManagementService", Mock(return_value=service))
    monkeypatch.setattr(admins, "get_supabase_admin_client", Mock())

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admins",
        "headers": [],
        "query_string": b"",
        "session": {"_csrf_token": "test-csrf-token"},
    }
    req = Request(scope)
    super_admin = AdminSession(
        user_id="super-user-1",
        email="super@cleanlink.co.za",
        role=AdminRole.SUPER_ADMIN,
        display_name="Super Administrator",
    )
    resp = _render(req, super_admin)
    assert resp.status_code == 200
    html = resp.body.decode("utf-8")
    assert "Platform Administrators & Access Control" in html
    assert "Review Verification Documents" in html
    assert "Manage Platform Settings" in html


def test_logout_redirects_to_login() -> None:
    response = TestClient(app).post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("failure,status_code", [
    (AuthenticationError("Invalid email or password."), 401),
    (AuthenticationUnavailableError("Admin sign-in is temporarily unavailable."), 503),
    (AuthenticationRateLimitError("Too many sign-in attempts."), 429),
    (PermissionDeniedError("This account is not an active CleanLink administrator."), 403),
])
def test_login_failure_returns_the_correct_status_without_authorizing(monkeypatch, failure, status_code):
    service = Mock()
    service.sign_in.side_effect = failure
    monkeypatch.setattr(auth, "AuthService", Mock(return_value=service))
    monkeypatch.setattr(auth, "get_supabase_admin_client", Mock())
    monkeypatch.setattr(auth, "get_supabase_auth_client", Mock())
    client = TestClient(app)
    response = client.post("/login", data={"email": "admin@example.test", "password": "test-password-only"})

    assert response.status_code == status_code
    assert str(failure) in response.text
    assert "test-password-only" not in response.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_activity_template_renders_admin_name_and_target_name() -> None:
    from starlette.requests import Request
    from app.core.templates import templates

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/activity",
        "headers": [],
        "query_string": b"",
        "session": {"_csrf_token": "test-csrf-token"},
    }
    req = Request(scope)
    admin = AdminSession(
        user_id="super-user-1",
        email="super@cleanlink.co.za",
        role=AdminRole.SUPER_ADMIN,
        display_name="Super Administrator",
    )
    mock_result = {
        "rows": [
            {
                "id": "act-1",
                "actor_name": "Nqobani Gumede",
                "actor_email": "22354757@live.mut.ac.za",
                "actor_role_label": "Super Admin",
                "actor_id": "07a5d5a2-943f-4036-acbf-6c62d1d47247",
                "action": "CLEANER_VERIFIED",
                "target_type": "cleaner_profiles",
                "target_name": "Zonke Cele",
                "target_id": "10813765-891a-4907-9385-b8b14ac3ef8f",
                "display_reason": "All verification documents inspected and approved",
                "created_at": "2026-10-19T10:15:56.752871+00:00",
            }
        ],
        "count": 1,
        "pages": 1,
    }
    resp = templates.TemplateResponse(
        req,
        "activity.html",
        {
            "request": req,
            "admin": admin,
            "active_page": "activity",
            "result": mock_result,
            "event_type": "",
            "actor_id": "",
            "target_type": "",
            "page": 1,
            "error": None,
        },
    )
    assert resp.status_code == 200
    html = resp.body.decode("utf-8")
    assert "Administrator / Actor" in html
    assert "Nqobani Gumede" in html
    assert "22354757@live.mut.ac.za" in html
    assert "Super Admin" in html
    assert "Zonke Cele" in html
    assert "All verification documents inspected and approved" in html


def test_cleaner_document_download_redirects_unauthenticated() -> None:
    response = TestClient(app).get("/cleaners/cid-123/documents/doc-456/download", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_verification_document_download_redirects_unauthenticated() -> None:
    response = TestClient(app).get("/verification/documents/doc-456/download", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_cleaners_template_renders_with_documents() -> None:
    from starlette.requests import Request
    from app.core.templates import templates

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/cleaners",
        "headers": [],
    }
    req = Request(scope)
    admin = AdminSession(
        user_id="super-user-1",
        email="super@cleanlink.co.za",
        role=AdminRole.SUPER_ADMIN,
        display_name="Super Administrator",
    )
    mock_cleaners = [
        {
            "id": "cleaner-123",
            "verification_status": "VERIFIED",
            "subscription_status": "ACTIVE",
            "availability_status": "AVAILABLE",
            "overall_rating": 4.8,
            "completed_jobs_count": 12,
            "profiles": {"first_name": "Ayanda", "last_name": "Sibisi"},
            "documents": [
                {
                    "id": "doc-1",
                    "cleaner_id": "cleaner-123",
                    "document_type_name": "Identity Document",
                    "original_file_name": "id_card.pdf",
                    "file_size_display": "240 KB",
                    "status": "APPROVED",
                    "is_pdf": True,
                    "is_image": False,
                    "signed_url": "https://example.com/signed/id_card.pdf",
                    "download_url": "/cleaners/cleaner-123/documents/doc-1/download",
                }
            ],
            "visibility_checks": [],
            "visible_to_clients": True,
        }
    ]
    resp = templates.TemplateResponse(
        req,
        "cleaners.html",
        {
            "request": req,
            "admin": admin,
            "active_page": "cleaners",
            "cleaners": mock_cleaners,
            "error": None,
        },
    )
    assert resp.status_code == 200
    html = resp.body.decode("utf-8")
    assert "Ayanda Sibisi" in html
    assert "Identity Document" in html
    assert "id_card.pdf" in html
    assert "/cleaners/cleaner-123/documents/doc-1/download" in html
    assert "Download Document" in html
    assert "documentPreviewModal" in html

