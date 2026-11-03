import argparse
from getpass import GetPassWarning, getpass
from pathlib import Path
import sys
import warnings

from pydantic import EmailStr, TypeAdapter, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.supabase import get_supabase_admin_client
from app.exceptions import AdminManagementError
from app.services.admin_management_service import reset_admin_password


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Privately reset an existing CleanLink administrator password.")
    parser.add_argument("--email", required=True, help="Existing administrator email address")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        raise SystemExit("Run this command in your own interactive terminal. Passwords cannot be piped or supplied as arguments.")
    try:
        email = str(TypeAdapter(EmailStr).validate_python(args.email.strip()))
    except ValidationError:
        raise SystemExit("Enter a valid administrator email address.") from None

    print(f"Reset the password for administrator {email}.")
    print("Client/cleaner accounts and account permissions will not be changed.")
    print("Existing admin dashboard sessions are not revoked by this command.")
    try:
        if input("Continue? [y/N] ").strip().lower() != "y":
            print("Cancelled. No password was changed.")
            return
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("New admin password (at least 12 characters): ")
            confirmation = getpass("Confirm new password: ")
    except (GetPassWarning, EOFError, KeyboardInterrupt):
        raise SystemExit("Cancelled. A private password prompt is required; no password was changed.") from None
    if password != confirmation:
        raise SystemExit("Passwords do not match. No password was changed.")
    if len(password) < 12 or not password.strip():
        raise SystemExit("Choose a password containing at least 12 characters. No password was changed.")

    try:
        client = get_supabase_admin_client()
    except Exception:
        raise SystemExit("The admin backend is not configured or could not be opened. No password was changed.") from None
    try:
        reset_admin_password(client, email, password)
    except AdminManagementError as exc:
        raise SystemExit(str(exc)) from None
    print("Admin password updated. Sign in through the CleanLink Admin dashboard.")


if __name__ == "__main__":
    main()
