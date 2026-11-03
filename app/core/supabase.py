from functools import lru_cache
from typing import Any

from supabase import Client, ClientOptions, create_client

from app.config import get_settings


@lru_cache
def _init_supabase_admin_client() -> Client:
    """Create the privileged server-side Supabase client for admin workflows."""
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
        options=ClientOptions(auto_refresh_token=False, persist_session=False),
    )


def get_supabase_auth_client() -> Client:
    """Return a fresh login client; user sessions must never replace server authorization."""
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
        options=ClientOptions(auto_refresh_token=False, persist_session=False),
    )


def get_supabase_admin_client() -> Client:
    """Retrieve the privileged server-side Supabase client for admin workflows.

    Guarantees the client is always authorized with the service_role key,
    preventing user tokens from downgrading privileges.
    """
    settings = get_settings()
    client = _init_supabase_admin_client()
    expected_auth = f"Bearer {settings.supabase_service_role_key}"
    if client.options.headers.get("Authorization") != expected_auth:
        client.options.headers["Authorization"] = expected_auth
    if getattr(client, "auth", None) and getattr(client.auth, "_headers", None):
        client.auth._headers["Authorization"] = expected_auth
    if getattr(client, "_postgrest", None):
        client._postgrest.auth(settings.supabase_service_role_key)
    return client


def unwrap_response(response: Any) -> Any:
    """Return Supabase response data while keeping repository code compact."""

    if hasattr(response, "data"):
        return response.data
    return response