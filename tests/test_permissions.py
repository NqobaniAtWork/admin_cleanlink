from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.models.enums import AdminRole
from app.schemas.auth_schema import AdminSession
from app.security.roles import has_permission, get_effective_permissions, ALL_OPERATIONAL_PERMISSIONS


def test_super_admin_has_settings_access():
    assert has_permission(AdminRole.SUPER_ADMIN, "settings:manage")
    assert has_permission(AdminRole.SUPER_ADMIN, "admins:manage")


def test_verification_admin_cannot_manage_settings():
    assert not has_permission(AdminRole.VERIFICATION_ADMIN, "settings:manage")
    assert not has_permission(AdminRole.VERIFICATION_ADMIN, "admins:manage")


def test_finance_admin_can_review_payments():
    assert has_permission(AdminRole.FINANCE_ADMIN, "payments:review")


def test_support_admin_can_view_bookings_but_not_refunds():
    assert has_permission(AdminRole.SUPPORT_ADMIN, "bookings:view")
    assert not has_permission(AdminRole.SUPPORT_ADMIN, "refunds:manage")


def test_custom_delegated_privilege_grants_access():
    # Support admin granted refunds:manage privilege by Super Admin
    granted = ["bookings:view", "refunds:manage"]
    assert has_permission(AdminRole.SUPPORT_ADMIN, "refunds:manage", custom_permissions=granted)
    assert has_permission(AdminRole.SUPPORT_ADMIN, "bookings:view", custom_permissions=granted)
    assert not has_permission(AdminRole.SUPPORT_ADMIN, "settings:manage", custom_permissions=granted)


def test_custom_revocation_denies_access():
    # Finance admin stripped of refunds:manage privilege
    restricted = ["finance:view", "payments:review"]
    assert not has_permission(AdminRole.FINANCE_ADMIN, "refunds:manage", custom_permissions=restricted)
    assert has_permission(AdminRole.FINANCE_ADMIN, "payments:review", custom_permissions=restricted)


def test_super_admin_exclusive_rights_cannot_be_delegated():
    # Non-super admin cannot be given admins:manage even if included in custom list
    attempted = ["admins:manage", "cleaners:manage"]
    assert not has_permission(AdminRole.SUPPORT_ADMIN, "admins:manage", custom_permissions=attempted)
    assert has_permission(AdminRole.SUPPORT_ADMIN, "cleaners:manage", custom_permissions=attempted)


def test_admin_session_permission_check():
    session = AdminSession(
        user_id="user-123",
        email="test@cleanlink.co.za",
        role=AdminRole.SUPPORT_ADMIN,
        custom_permissions=["verification:review", "cleaners:manage"],
    )
    assert session.has_permission("verification:review")
    assert session.has_permission("cleaners:manage")
    assert not session.has_permission("settings:manage")
    assert not session.has_permission("admins:manage")
