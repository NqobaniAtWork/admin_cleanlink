import argparse
from getpass import getpass
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.supabase import get_supabase_admin_client
from app.exceptions import AdminManagementError
from app.models.enums import AdminRole
from app.schemas.auth_schema import AdminCreate
from app.services.admin_management_service import bootstrap_super_admin


def main() -> None:
    parser = argparse.ArgumentParser(description="Create CleanLink's first Supabase super admin.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--first-name", required=True)
    parser.add_argument("--last-name", required=True)
    parser.add_argument("--password", required=False, default=None, help="Super-admin password (min 12 chars)")
    args = parser.parse_args()
    if args.password:
        password = args.password
    else:
        password = getpass("Super-admin password: ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")
    payload = AdminCreate(email=args.email, password=password, first_name=args.first_name, last_name=args.last_name, role=AdminRole.SUPER_ADMIN)
    try:
        profile = bootstrap_super_admin(get_supabase_admin_client(), payload)
    except AdminManagementError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Super admin created successfully (admin profile {profile['id']}).")


if __name__ == "__main__":
    main()