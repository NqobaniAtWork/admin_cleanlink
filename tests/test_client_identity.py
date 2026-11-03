import os
import re
from unittest.mock import Mock

import pytest

os.environ.setdefault("CLEANLINK_ADMIN_SESSION_SECRET", "synthetic-session-secret-with-at-least-32-characters")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SECRET_KEY", "mock-supabase-secret-key-fixture")

from fastapi.testclient import TestClient
from app.main import app
from app.api import client_identity
from app.models.enums import AdminRole
from app.schemas.auth_schema import AdminSession
from app.security.roles import has_permission
from app.services.client_identity_service import ClientIdentityAdminService

ADMIN="11111111-1111-4111-8111-111111111111"
REVIEW="22222222-2222-4222-8222-222222222222"
TASK="33333333-3333-4333-8333-333333333333"

@pytest.fixture
def identity_admin(monkeypatch):
    actor=AdminSession(user_id=ADMIN,admin_profile_id=ADMIN,email="synthetic@example.test",role=AdminRole.VERIFICATION_ADMIN,display_name="Synthetic reviewer")
    service=Mock()
    service.overview.return_value={"states":[],"attempts":[],"reviews":[{"id":REVIEW,"user_id":ADMIN,"status":"pending","reason":"declined","client_note":"<script>synthetic unsafe note</script>","created_at":"2026-10-21"}],"deletions":[{"id":TASK,"provider":"smile_id","provider_subject":"synthetic-subject","reason":"withdrawal","created_at":"2026-10-21"}]}
    monkeypatch.setattr(client_identity,"current_admin",lambda request:actor)
    monkeypatch.setattr(client_identity,"ClientIdentityAdminService",Mock(return_value=service))
    monkeypatch.setattr(client_identity,"get_supabase_admin_client",Mock())
    return actor,service,TestClient(app)


def form_token(client):
    response=client.get("/identity-verification")
    assert response.status_code==200
    match=re.search(r'name="form_token" value="([^"]+)"',response.text)
    assert match
    return match.group(1)


def test_anonymous_cannot_open_review_queue(monkeypatch):
    monkeypatch.setattr(client_identity,"current_admin",lambda request:None)
    response=TestClient(app).get("/identity-verification",follow_redirects=False)
    assert response.status_code==303
    assert response.headers["location"]=="/login"


@pytest.mark.parametrize("role",[AdminRole.FINANCE_ADMIN,AdminRole.SUPPORT_ADMIN])
def test_unapproved_staff_cannot_view_or_decide(identity_admin,role):
    actor,service,client=identity_admin
    actor.role=role
    assert client.get("/identity-verification").status_code==403
    response=client.post(f"/identity-verification/{REVIEW}/decision",data={"decision":"approve","evidence_reference":"SYNTHETIC-REF","reason":"Synthetic documented check","form_token":"invalid","confirmed":"true"})
    assert response.status_code==403
    service.overview.assert_not_called()
    service.decide.assert_not_called()


def test_queue_escapes_client_content_and_never_embeds_raw_capture(identity_admin):
    _,_,client=identity_admin
    response=client.get("/identity-verification")
    assert response.status_code==200
    assert "&lt;script&gt;" in response.text
    assert "<script>synthetic unsafe note</script>" not in response.text
    assert "No raw biometric captures" in response.text


@pytest.mark.parametrize("confirmed",[None,"false"])
def test_sensitive_decision_requires_confirmation(identity_admin,confirmed):
    _,service,client=identity_admin
    payload={"decision":"approve","evidence_reference":"SYNTHETIC-REF","reason":"Synthetic documented check","form_token":form_token(client)}
    if confirmed is not None:payload["confirmed"]=confirmed
    response=client.post(f"/identity-verification/{REVIEW}/decision",data=payload)
    assert response.status_code==400
    service.decide.assert_not_called()


def test_sensitive_decision_rejects_bad_csrf(identity_admin):
    _,service,client=identity_admin
    form_token(client)
    response=client.post(f"/identity-verification/{REVIEW}/decision",data={"decision":"approve","evidence_reference":"SYNTHETIC-REF","reason":"Synthetic documented check","form_token":"forged","confirmed":"true"})
    assert response.status_code==403
    service.decide.assert_not_called()


@pytest.mark.parametrize("decision,approved",[("approve",True),("reject",False)])
def test_review_uses_authenticated_reviewer_and_explicit_decision(identity_admin,decision,approved):
    _,service,client=identity_admin
    response=client.post(f"/identity-verification/{REVIEW}/decision",data={"decision":decision,"evidence_reference":"SYNTHETIC-REF","reason":"Synthetic documented check","form_token":form_token(client),"confirmed":"true"},follow_redirects=False)
    assert response.status_code==303
    service.decide.assert_called_once_with(REVIEW,ADMIN,approved,"SYNTHETIC-REF","Synthetic documented check")


def test_provider_deletion_requires_actual_confirmation(identity_admin):
    _,service,client=identity_admin
    token=form_token(client)
    assert client.post(f"/identity-verification/deletions/{TASK}/confirm",data={"form_token":token,"confirmation_reference":"SYNTHETIC-PURGE"}).status_code==400
    response=client.post(f"/identity-verification/deletions/{TASK}/confirm",data={"form_token":token,"confirmation_reference":"SYNTHETIC-PURGE","confirmed":"true"},follow_redirects=False)
    assert response.status_code==303
    service.confirm_deletion.assert_called_once_with(TASK,ADMIN,"SYNTHETIC-PURGE")


def test_provider_failure_does_not_leak_private_detail(identity_admin,caplog):
    _,service,client=identity_admin
    service.decide.side_effect=RuntimeError("SENSITIVE-PROVIDER-SECRET")
    response=client.post(f"/identity-verification/{REVIEW}/decision",data={"decision":"approve","evidence_reference":"SYNTHETIC-REF","reason":"Synthetic documented check","form_token":form_token(client),"confirmed":"true"})
    assert response.status_code==409
    assert "SENSITIVE-PROVIDER-SECRET" not in response.text+caplog.text


def test_service_only_calls_atomic_rpc_with_safe_metadata():
    client=Mock()
    ClientIdentityAdminService(client).decide(REVIEW,ADMIN,True,"SYNTHETIC-REF","Synthetic documented check")
    client.rpc.assert_called_once_with("decide_client_identity_review",{"p_review_id":REVIEW,"p_admin_user_id":ADMIN,"p_approve":True,"p_evidence_reference":"SYNTHETIC-REF","p_reason":"Synthetic documented check"})


def test_self_declaration_is_insufficient_for_admin_approval():
    client=Mock()
    with pytest.raises(ValueError):ClientIdentityAdminService(client).decide(REVIEW,ADMIN,True,"","yes")
    client.rpc.assert_not_called()


@pytest.mark.parametrize("permission",["identity_verification.view","identity_verification.review","identity_verification.approve","identity_verification.reject"])
def test_granular_permissions_do_not_extend_to_finance_or_support(permission):
    assert has_permission(AdminRole.SUPER_ADMIN,permission)
    assert has_permission(AdminRole.VERIFICATION_ADMIN,permission)
    assert not has_permission(AdminRole.FINANCE_ADMIN,permission)
    assert not has_permission(AdminRole.SUPPORT_ADMIN,permission)


@pytest.mark.parametrize("permission", ["identity_verification.view", "identity_verification.review", "identity_verification.approve", "identity_verification.reject"])
def test_identity_permissions_support_explicit_delegation_and_revocation(permission):
    from app.security.roles import ALL_OPERATIONAL_PERMISSIONS
    assert permission in ALL_OPERATIONAL_PERMISSIONS
    assert has_permission(AdminRole.SUPPORT_ADMIN, permission, [permission])
    assert not has_permission(AdminRole.VERIFICATION_ADMIN, permission, ["dashboard:view"])


def test_delegated_queue_access_does_not_grant_approval(identity_admin):
    actor, service, client = identity_admin
    actor.role = AdminRole.SUPPORT_ADMIN
    actor.custom_permissions = ["identity_verification.view", "identity_verification.review"]
    response = client.get("/identity-verification")
    assert response.status_code == 200
    assert 'href="/identity-verification"' in response.text
    assert "Approve account check" not in response.text
    assert "Reject account check" not in response.text
    assert "Record confirmed deletion" not in response.text
    denied = client.post(f"/identity-verification/{REVIEW}/decision", data={"decision": "approve", "evidence_reference": "SYNTHETIC-REF", "reason": "Synthetic documented check", "form_token": "unused", "confirmed": "true"})
    assert denied.status_code == 403
    service.decide.assert_not_called()


def test_revoked_verification_admin_cannot_view_queue(identity_admin):
    actor, service, client = identity_admin
    actor.custom_permissions = ["dashboard:view"]
    assert client.get("/identity-verification").status_code == 403
    service.overview.assert_not_called()
