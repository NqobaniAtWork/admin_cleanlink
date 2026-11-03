# CleanLink Standalone Admin

A standalone FastAPI admin application for CleanLink operations. It is separate from the Expo client/cleaner mobile app and connects to the shared Supabase backend using trusted server-side credentials.

## Capabilities

- Admin login backed by Supabase Auth and `admin_profiles`
- Role-permission checks for verification, finance, support, and super-admin workflows
- Operational dashboard metrics from live Supabase tables
- Cleaner management and suspension actions with audit logging
- Cleaner verification queue with approve/reject actions
- Annual subscription period calculation and activation workflow
- Booking, payment, refund, and settings monitoring pages
- Payment gateway registry for PayFast, PayGate, Ozow, Yoco, and Peach Payments

## Setup

1. Create a virtual environment.
2. Install dependencies: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in secure values. Use a server-only Supabase `sb_secret_...` key as `SUPABASE_SECRET_KEY`; the legacy `SUPABASE_SERVICE_ROLE_KEY` name remains supported during migration.
4. Run the app from this folder: `uvicorn app.main:app --reload --port 8000`

The admin resolves `cleanlink_admin/.env` regardless of the shell's current directory. Never expose `SUPABASE_SECRET_KEY` or `SUPABASE_SERVICE_ROLE_KEY` to the Expo app, browser JavaScript, or source control.
## Bootstrap the first super admin

Run this once, before any super admin exists:

```powershell
.\.venv\Scripts\python.exe cleanlink_admin\scripts\bootstrap_super_admin.py --email "admin@example.com" --first-name "First" --last-name "Last"
```

The command prompts for the password twice, so the password is not stored in source code or shell history. After login, a super admin can open **Administrators** to create accounts with the Super, Verification, Finance, or Support admin level.

## Recover an existing admin password

From `cleanlink-expo`, run in your own interactive PowerShell terminal:

```powershell
.\.venv\Scripts\python.exe -B .\cleanlink_admin\scripts\reset_admin_password.py --email "admin@example.com"
```

Confirm the target account, then enter the new password twice at the hidden prompts.
Passwords must contain at least 12 characters. Never put a password in command-line
arguments, chat, or source code. This trusted server-side command uses the admin's
existing `.env`; do not expose its server key to users or browser code.

Only a confirmed, active administrator can be reset. Client/cleaner accounts,
disabled accounts, administrator roles, and permissions are not changed. Existing
admin dashboard sessions are not revoked by this command. If a password was shared,
those sessions must also be invalidated separately; resetting a password alone must
not be treated as proof that everyone else has been signed out.


How to runn the admin side:
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
