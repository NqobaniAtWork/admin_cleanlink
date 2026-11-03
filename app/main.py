import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import client_identity, admins, auth, bookings, cleaners, dashboard, payments, refunds, settings, subscriptions, users, verification
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app() -> FastAPI:
    runtime_settings = get_settings()
    app = FastAPI(title="CleanLink Admin", version="0.1.0")
    app.add_middleware(
        SessionMiddleware,
        secret_key=runtime_settings.session_secret,
        same_site="lax",
        https_only=runtime_settings.secure_cookies,
    )
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(cleaners.router)
    app.include_router(verification.router)
    app.include_router(verification.client_router)
    app.include_router(client_identity.router)
    app.include_router(subscriptions.router)
    app.include_router(bookings.router)
    app.include_router(payments.router)
    app.include_router(refunds.router)
    app.include_router(settings.router)
    app.include_router(admins.router)
    app.include_router(users.router)
    return app


app = create_app()
