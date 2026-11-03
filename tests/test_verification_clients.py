from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

try:
    import pytest
except ImportError:
    pytest = None

from app.models.enums import AdminRole
from app.repositories.verification_repository import VerificationRepository
from app.schemas.auth_schema import AdminSession
from app.services.verification_service import VerificationService


def test_count_client_metrics_calculates_correct_breakdown():
    mock_client = Mock()

    # Profiles: 3 clients
    p_select = Mock()
    p_select.eq.return_value.execute.return_value.data = [
        {"id": "u1"},
        {"id": "u2"},
        {"id": "u3"},
    ]

    # Client identity verifications: u1 is verified, u2 is unverified
    civ_select = Mock()
    civ_select.execute.return_value.data = [
        {"user_id": "u1", "status": "verified"},
        {"user_id": "u2", "status": "unverified"},
    ]

    # Reviews: u2 has pending review
    rev_select = Mock()
    rev_select.eq.return_value.execute.return_value.data = [
        {"user_id": "u2", "status": "pending"},
    ]

    def table_router(name):
        t = Mock()
        if name == "profiles":
            t.select.return_value = p_select
        elif name == "client_identity_verifications":
            t.select.return_value = civ_select
        elif name == "identity_verification_reviews":
            t.select.return_value = rev_select
        return t

    mock_client.table.side_effect = table_router

    repo = VerificationRepository(mock_client)
    counts = repo.count_client_metrics()

    assert counts["all"] == 3
    assert counts["verified"] == 1
    assert counts["pending"] == 1  # u2 has pending review
    assert counts["unverified"] == 1  # u3 has no record or review -> unverified


def test_list_client_applications_enriches_and_filters():
    mock_client = Mock()

    # Profiles
    p_query = Mock()
    p_query.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {
            "id": "c-101",
            "first_name": "Sipho",
            "last_name": "Khumalo",
            "phone": "+27821112222",
            "profile_image_path": "avatars/sipho.jpg",
            "account_status": "ACTIVE",
            "created_at": "2026-10-01T10:00:00Z",
            "role": "CLIENT",
        },
        {
            "id": "c-102",
            "first_name": "Thandi",
            "last_name": "Ndlovu",
            "phone": "+27833334444",
            "profile_image_path": None,
            "account_status": "PENDING",
            "created_at": "2026-10-02T10:00:00Z",
            "role": "CLIENT",
        },
    ]

    # client_profiles
    cp_query = Mock()
    cp_query.in_.return_value.execute.return_value.data = [
        {"id": "cp-1", "user_id": "c-101", "default_address_id": "addr-1"},
        {"id": "cp-2", "user_id": "c-102", "default_address_id": None},
    ]

    # addresses
    addr_query = Mock()
    addr_query.in_.return_value.execute.return_value.data = [
        {"user_id": "c-101", "suburb": "Rosebank", "address_line_1": "10 Oxford Rd"},
    ]

    # client_identity_verifications
    civ_query = Mock()
    civ_query.in_.return_value.execute.return_value.data = [
        {"user_id": "c-101", "status": "verified", "method": "manual", "verified_at": "2026-10-05T00:00:00Z", "expires_at": "2027-10-05T00:00:00Z"},
    ]

    # identity_verification_reviews
    rev_query = Mock()
    rev_query.in_.return_value.order.return_value.execute.return_value.data = [
        {
            "id": "rev-1",
            "user_id": "c-102",
            "status": "pending",
            "reason": "camera",
            "client_note": "Camera was blurry on previous phone, requesting manual review.",
            "created_at": "2026-10-02T11:00:00Z",
        }
    ]

    # bookings
    b_query = Mock()
    b_query.in_.return_value.order.return_value.limit.return_value.execute.return_value.data = []

    def table_router(name):
        t = Mock()
        if name == "profiles":
            t.select.return_value = p_query
        elif name == "client_profiles":
            t.select.return_value = cp_query
        elif name == "addresses":
            t.select.return_value = addr_query
        elif name == "client_identity_verifications":
            t.select.return_value = civ_query
        elif name == "identity_verification_reviews":
            t.select.return_value = rev_query
        elif name == "bookings":
            t.select.return_value = b_query
        return t

    mock_client.table.side_effect = table_router
    mock_client.auth.admin.list_users.return_value = [
        {"id": "c-101", "email": "sipho@example.com", "user_metadata": {"idNumber": "9001015000080"}},
        {"id": "c-102", "email": "thandi@example.com", "user_metadata": {"identityNumber": "9502025000081"}},
    ]

    repo = VerificationRepository(mock_client)
    repo.get_signed_url = Mock(return_value="https://signed.example.com/sipho.jpg")

    all_apps = repo.list_client_applications(status_filter="all")
    assert len(all_apps) == 2
    assert all_apps[0]["first_name"] == "Sipho"
    assert all_apps[0]["status"] == "VERIFIED"
    assert all_apps[0]["address"] == "Rosebank"
    assert all_apps[0]["identity_number"] == "9001015000080"
    assert all_apps[0]["auth_email"] == "sipho@example.com"

    assert all_apps[1]["first_name"] == "Thandi"
    assert all_apps[1]["status"] == "PENDING_REVIEW"
    assert all_apps[1]["review"]["client_note"] == "Camera was blurry on previous phone, requesting manual review."

    pending_apps = repo.list_client_applications(status_filter="pending")
    assert len(pending_apps) == 1
    assert pending_apps[0]["id"] == "c-102"

    verified_apps = repo.list_client_applications(status_filter="verified")
    assert len(verified_apps) == 1
    assert verified_apps[0]["id"] == "c-101"

    searched_apps = repo.list_client_applications(status_filter="all", search="thandi")
    assert len(searched_apps) == 1
    assert searched_apps[0]["id"] == "c-102"


def test_approve_client_updates_verification_and_audits():
    mock_client = Mock()
    civ_table = Mock()
    civ_table.upsert.return_value.execute.return_value = None

    rev_table = Mock()
    rev_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = None

    prof_table = Mock()
    prof_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = None

    def table_router(name):
        if name == "client_identity_verifications":
            return civ_table
        elif name == "identity_verification_reviews":
            return rev_table
        elif name == "profiles":
            return prof_table
        return Mock()

    mock_client.table.side_effect = table_router

    service = VerificationService(mock_client)
    service.audit = Mock()
    service.approve_client(
        admin_user_id="admin-123",
        admin_profile_id="ap-456",
        user_id="user-789",
        reason="Approved after document inspection",
        evidence_reference="EVID-12345",
    )

    civ_table.upsert.assert_called_once()
    upsert_args = civ_table.upsert.call_args[0][0]
    assert upsert_args["user_id"] == "user-789"
    assert upsert_args["status"] == "verified"
    assert upsert_args["method"] == "manual"
    assert upsert_args["policy_version"] == "manual_admin"
    assert upsert_args["verified_at"] is not None
    assert upsert_args["expires_at"] is not None

    service.audit.record.assert_called_once()
    action = service.audit.record.call_args[0][0]
    assert action.action == "CLIENT_VERIFIED"
    assert action.target_id == "user-789"


def test_reject_client_updates_verification_and_audits():
    mock_client = Mock()
    civ_table = Mock()
    civ_table.upsert.return_value.execute.return_value = None

    rev_table = Mock()
    rev_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = None

    def table_router(name):
        if name == "client_identity_verifications":
            return civ_table
        elif name == "identity_verification_reviews":
            return rev_table
        return Mock()

    mock_client.table.side_effect = table_router

    service = VerificationService(mock_client)
    service.audit = Mock()
    service.reject_client(
        admin_user_id="admin-123",
        admin_profile_id="ap-456",
        user_id="user-789",
        reason="ID details mismatch client profile",
    )

    civ_table.upsert.assert_called_once()
    upsert_args = civ_table.upsert.call_args[0][0]
    assert upsert_args["user_id"] == "user-789"
    assert upsert_args["status"] == "rejected"

    service.audit.record.assert_called_once()
    action = service.audit.record.call_args[0][0]
    assert action.action == "CLIENT_REJECTED"
    assert action.reason == "ID details mismatch client profile"


def test_reset_client_reverts_to_unverified():
    mock_client = Mock()
    civ_table = Mock()
    civ_table.upsert.return_value.execute.return_value = None

    def table_router(name):
        if name == "client_identity_verifications":
            return civ_table
        return Mock()

    mock_client.table.side_effect = table_router

    service = VerificationService(mock_client)
    service.audit = Mock()
    service.reset_client(
        admin_user_id="admin-123",
        admin_profile_id="ap-456",
        user_id="user-789",
        reason="Reset per customer support request",
    )

    civ_table.upsert.assert_called_once()
    upsert_args = civ_table.upsert.call_args[0][0]
    assert upsert_args["user_id"] == "user-789"
    assert upsert_args["status"] == "unverified"
    assert upsert_args["verified_at"] is None
    assert upsert_args["expires_at"] is None

    service.audit.record.assert_called_once()
    action = service.audit.record.call_args[0][0]
    assert action.action == "CLIENT_VERIFICATION_RESET"


def test_verification_queue_route_with_client_entity():
    from app.api import verification
    from starlette.requests import Request

    mock_service = Mock()
    mock_service.count_metrics.return_value = {"all": 10, "pending": 2, "verified": 8, "rejected": 0}
    mock_service.count_client_metrics.return_value = {"all": 5, "pending": 1, "verified": 3, "rejected": 0, "unverified": 1}
    mock_service.list_client_applications.return_value = [
        {
            "id": "c-1",
            "user_id": "c-1",
            "first_name": "Lindiwe",
            "last_name": "Dlamini",
            "phone": "+27829990000",
            "email": "lindiwe@example.com",
            "account_status": "ACTIVE",
            "status": "PENDING_REVIEW",
            "status_raw": "pending",
            "created_at": "2026-10-15T00:00:00Z",
            "review": None,
            "profile_image_url": None,
            "face_photo_url": None,
        }
    ]

    with patch.object(verification, "current_admin") as mock_admin,          patch.object(verification, "VerificationService", return_value=mock_service),          patch.object(verification, "get_supabase_admin_client", return_value=Mock()),          patch.object(verification.templates, "TemplateResponse") as mock_render:

        mock_admin.return_value = AdminSession(
            user_id="admin-1",
            email="admin@cleanlink.co.za",
            role=AdminRole.SUPER_ADMIN,
        )

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/verification",
            "headers": [],
            "query_string": b"entity=client&status=pending",
        }
        req = Request(scope)

        verification.verification_queue(req)

        mock_render.assert_called_once()
        context = mock_render.call_args[0][2]
        assert context["entity"] == "client"
        assert context["selected_status"] == "pending"
        assert len(context["applications"]) == 1
        assert context["applications"][0]["first_name"] == "Lindiwe"
        assert context["client_counts"]["pending"] == 1


def test_approve_client_route_invokes_service():
    from app.api import verification
    from starlette.requests import Request

    mock_service = Mock()

    with patch.object(verification, "current_admin") as mock_admin,          patch.object(verification, "VerificationService", return_value=mock_service),          patch.object(verification, "get_supabase_admin_client", return_value=Mock()):

        mock_admin.return_value = AdminSession(
            user_id="admin-super",
            admin_profile_id="ap-super",
            email="super@cleanlink.co.za",
            role=AdminRole.SUPER_ADMIN,
        )

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/verification/clients/client-99/approve",
            "headers": [],
        }
        req = Request(scope)

        resp = verification.approve_client(
            req,
            user_id="client-99",
            reason="Verified client ID",
            evidence_reference="REF-001",
        )

        assert resp.status_code == 303
        assert resp.headers["location"] == "/verification?entity=client&success=Client+verified+and+approved+successfully."
        mock_service.approve_client.assert_called_once_with(
            "admin-super",
            "ap-super",
            "client-99",
            reason="Verified client ID",
            evidence_reference="REF-001",
        )


def test_reject_client_route_invokes_service():
    from app.api import verification
    from starlette.requests import Request

    mock_service = Mock()

    with patch.object(verification, "current_admin") as mock_admin,          patch.object(verification, "VerificationService", return_value=mock_service),          patch.object(verification, "get_supabase_admin_client", return_value=Mock()):

        mock_admin.return_value = AdminSession(
            user_id="admin-super",
            admin_profile_id="ap-super",
            email="super@cleanlink.co.za",
            role=AdminRole.SUPER_ADMIN,
        )

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/verification/clients/client-99/reject",
            "headers": [],
        }
        req = Request(scope)

        resp = verification.reject_client(
            req,
            user_id="client-99",
            reason="ID expired",
        )

        assert resp.status_code == 303
        assert resp.headers["location"] == "/verification?entity=client&success=Client+verification+has+been+rejected."
        mock_service.reject_client.assert_called_once_with(
            "admin-super",
            "ap-super",
            "client-99",
            reason="ID expired",
        )


def test_reset_client_route_invokes_service():
    from app.api import verification
    from starlette.requests import Request

    mock_service = Mock()

    with patch.object(verification, 'current_admin') as mock_admin, \
         patch.object(verification, 'VerificationService', return_value=mock_service), \
         patch.object(verification, 'get_supabase_admin_client', return_value=Mock()):

        mock_admin.return_value = AdminSession(
            user_id='admin-super',
            admin_profile_id='ap-super',
            email='super@cleanlink.co.za',
            role=AdminRole.SUPER_ADMIN,
        )

        scope = {
            'type': 'http',
            'method': 'POST',
            'path': '/verification/clients/client-99/reset',
            'headers': [],
        }
        req = Request(scope)

        resp = verification.reset_client(
            req,
            user_id='client-99',
            reason='User requested re-verification',
        )

        assert resp.status_code == 303
        assert resp.headers['location'] == '/verification?entity=client&success=Client+verification+reset+to+unverified.'
        mock_service.reset_client.assert_called_once_with(
            'admin-super',
            'ap-super',
            'client-99',
            reason='User requested re-verification',
        )


def test_client_documents_loaded_and_assigned_to_slots():
    mock_client = Mock()

    # Client profile with user profile
    cp_query = Mock()
    cp_query.execute.return_value.data = [
        {
            "id": "cp-1",
            "user_id": "u-1",
            "verification_status": "PENDING_REVIEW",
            "submitted_at": "2026-10-30T10:00:00Z",
            "verified_at": None,
            "verified_by": None,
            "rejection_reason": None,
            "profiles": {
                "id": "u-1",
                "first_name": "Noluntu",
                "last_name": "Mbambo",
                "phone": "+27651234567",
                "profile_image_path": None,
                "account_status": "PENDING",
                "created_at": "2026-10-30T10:00:00Z",
                "role": "CLIENT",
            },
        }
    ]

    # Documents query
    doc_query = Mock()
    doc_query.in_.return_value.order.return_value.execute.return_value.data = [
        {
            "id": "doc-id-1",
            "client_id": "cp-1",
            "document_type_key": "IDENTITY_DOCUMENT",
            "original_file_name": "id_copy.pdf",
            "mime_type": "application/pdf",
            "file_size": 150000,
            "storage_path": "u-1/identity_document/id.pdf",
            "status": "SUBMITTED",
        },
        {
            "id": "doc-res-1",
            "client_id": "cp-1",
            "document_type_key": "PROOF_OF_RESIDENCE",
            "original_file_name": "utility_bill.pdf",
            "mime_type": "application/pdf",
            "file_size": 250000,
            "storage_path": "u-1/proof_of_residence/bill.pdf",
            "status": "SUBMITTED",
        },
    ]

    # Applications query
    cva_query = Mock()
    cva_query.in_.return_value.order.return_value.execute.return_value.data = [
        {"id": "app-1", "client_id": "cp-1", "status": "PENDING_REVIEW"}
    ]

    # Addresses query
    addr_query = Mock()
    addr_query.in_.return_value.execute.return_value.data = []

    def table_router(name):
        t = Mock()
        if name == "client_profiles":
            t.select.return_value = cp_query
        elif name == "client_verification_documents":
            t.select.return_value = doc_query
        elif name == "client_verification_applications":
            t.select.return_value = cva_query
        elif name == "addresses":
            t.select.return_value = addr_query
        return t

    mock_client.table.side_effect = table_router
    mock_client.storage.from_.return_value.create_signed_url.return_value = {"signedURL": "https://signed.url/doc"}

    repo = VerificationRepository(mock_client)
    apps = repo.list_client_applications(status_filter="all")

    assert len(apps) == 1
    app = apps[0]
    assert app["first_name"] == "Noluntu"
    assert app["status"] == "PENDING_REVIEW"
    assert app["id_document"] is not None
    assert app["id_document"]["original_file_name"] == "id_copy.pdf"
    assert app["proof_of_residence"] is not None
    assert app["proof_of_residence"]["original_file_name"] == "utility_bill.pdf"
    assert app["is_complete"] is True
