from typing import Any
from app.models.enums import AdminRole


PERMISSIONS_CATALOG: list[dict[str, Any]] = [
    {
        "category": "Client Account Verification",
        "icon": "ID",
        "permissions": [
            {"id": "identity_verification.view", "name": "View Client Account Checks", "description": "View protected client account-check metadata and review cases."},
            {"id": "identity_verification.review", "name": "Review Client Account Checks", "description": "Review the approved alternative account-holder check and its evidence reference."},
            {"id": "identity_verification.approve", "name": "Approve Client Account Checks", "description": "Approve documented checks and record confirmed provider erasure."},
            {"id": "identity_verification.reject", "name": "Reject Client Account Checks", "description": "Reject documented account-check review cases."},
        ],
    },
    {
        "category": "Cleaners & Verification",
        "icon": "🪪",
        "permissions": [
            {
                "id": "verification:review",
                "name": "Review Verification Documents",
                "description": "Inspect ID, address, and criminal clearance documents; approve or reject cleaner verification applications.",
            },
            {
                "id": "cleaners:manage",
                "name": "Manage Cleaner Accounts",
                "description": "Suspend, reinstate, or modify cleaner profile settings and approval statuses.",
            },
            {
                "id": "cleaners:view",
                "name": "View Cleaners Directory",
                "description": "Browse and search the cleaner roster, ratings, tiers, and service histories.",
            },
        ],
    },
    {
        "category": "Clients & Users",
        "icon": "👥",
        "permissions": [
            {
                "id": "clients:manage",
                "name": "Manage Client Accounts",
                "description": "Suspend, reactivate, or update client profile account statuses.",
            },
            {
                "id": "users:view",
                "name": "View Users Directory",
                "description": "Browse and search all registered clients, cleaners, and user profiles.",
            },
            {
                "id": "audit:view",
                "name": "View Audit & Activity Logs",
                "description": "Inspect chronological administrative actions, system events, and audit trails.",
            },
        ],
    },
    {
        "category": "Bookings & Operations",
        "icon": "📅",
        "permissions": [
            {
                "id": "bookings:manage",
                "name": "Manage Bookings & Schedules",
                "description": "Cancel, reschedule, reassign, or adjust customer service bookings.",
            },
            {
                "id": "bookings:view",
                "name": "View Bookings & Timelines",
                "description": "Inspect booking lists, statuses, job progression timelines, and booking details.",
            },
        ],
    },
    {
        "category": "Payments & Finances",
        "icon": "💳",
        "permissions": [
            {
                "id": "refunds:manage",
                "name": "Process & Approve Refunds",
                "description": "Review, approve, reject, or execute client refund requests.",
            },
            {
                "id": "finance:manage",
                "name": "Financial & Payout Operations",
                "description": "Manage payouts, post ledger adjustments, and manage commission deductions.",
            },
            {
                "id": "payments:review",
                "name": "Review Payment Transactions",
                "description": "Inspect transactions, incoming payments, and gateway reference records.",
            },
            {
                "id": "finance:view",
                "name": "View Financial Overview",
                "description": "View platform revenue totals, commission reports, and transaction balances.",
            },
        ],
    },
    {
        "category": "Subscriptions",
        "icon": "🔁",
        "permissions": [
            {
                "id": "subscriptions:manage",
                "name": "Manage Subscriptions",
                "description": "Activate, cancel, or override cleaner subscription plans and billing cycles.",
            },
            {
                "id": "subscriptions:view",
                "name": "View Subscriptions",
                "description": "View subscribed cleaners, upcoming renewals, and plan pricing tiers.",
            },
        ],
    },
    {
        "category": "Platform Configuration",
        "icon": "⚙️",
        "permissions": [
            {
                "id": "settings:manage",
                "name": "Manage Platform Settings",
                "description": "Modify payment gateways, commission rates, and app-wide operating switches.",
            },
            {
                "id": "settings:view",
                "name": "View Settings",
                "description": "Read platform configuration and payment gateway parameters.",
            },
        ],
    },
]

ALL_OPERATIONAL_PERMISSIONS: set[str] = {
    item["id"] for cat in PERMISSIONS_CATALOG for item in cat.get("permissions", [])
}

PERMISSION_LABELS: dict[str, str] = {
    item["id"]: item["name"] for cat in PERMISSIONS_CATALOG for item in cat.get("permissions", [])
}
PERMISSION_LABELS["admins:manage"] = "Manage Administrators & Privileges"
PERMISSION_LABELS["dashboard:view"] = "Dashboard Access"


ROLE_PERMISSIONS: dict[AdminRole, set[str]] = {
    AdminRole.SUPER_ADMIN: ALL_OPERATIONAL_PERMISSIONS | {"dashboard:view", "admins:manage"},
    AdminRole.VERIFICATION_ADMIN: {
        "identity_verification.view",
        "identity_verification.review",
        "identity_verification.approve",
        "identity_verification.reject",
        "dashboard:view",
        "users:view",
        "cleaners:view",
        "cleaners:manage",
        "verification:review",
        "bookings:view",
        "audit:view",
    },
    AdminRole.FINANCE_ADMIN: {
        "dashboard:view",
        "users:view",
        "bookings:view",
        "subscriptions:view",
        "subscriptions:manage",
        "finance:view",
        "finance:manage",
        "payments:review",
        "refunds:manage",
        "audit:view",
    },
    AdminRole.SUPPORT_ADMIN: {
        "dashboard:view",
        "users:view",
        "cleaners:view",
        "cleaners:manage",
        "clients:manage",
        "bookings:view",
        "bookings:manage",
        "audit:view",
    },
}


def get_effective_permissions(
    role: AdminRole | str,
    custom_permissions: list[str] | set[str] | None = None,
) -> set[str]:
    role_str = role.value if isinstance(role, AdminRole) else str(role)
    if role_str == AdminRole.SUPER_ADMIN.value:
        return ALL_OPERATIONAL_PERMISSIONS | {"dashboard:view", "admins:manage"}

    # If explicit custom_permissions are provided (e.g. granted by Super Admin)
    if custom_permissions is not None and len(custom_permissions) > 0:
        cleaned = {p for p in custom_permissions if p != "admins:manage"}
        cleaned.add("dashboard:view")
        return cleaned

    # Fall back to base role permissions
    role_enum = role if isinstance(role, AdminRole) else None
    if not role_enum and role_str in AdminRole._value2member_map_:
        role_enum = AdminRole(role_str)

    if role_enum and role_enum in ROLE_PERMISSIONS:
        return set(ROLE_PERMISSIONS[role_enum])

    return {"dashboard:view"}


def has_permission(
    role: AdminRole | str,
    permission: str,
    custom_permissions: list[str] | set[str] | None = None,
) -> bool:
    role_str = role.value if isinstance(role, AdminRole) else str(role)
    if role_str == AdminRole.SUPER_ADMIN.value:
        return True

    # Managing administrators is strictly reserved for Super Admin
    if permission == "admins:manage":
        return False

    effective = get_effective_permissions(role, custom_permissions)
    return permission in effective