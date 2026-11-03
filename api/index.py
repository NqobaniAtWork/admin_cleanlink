import json
import os
import sys
from pathlib import Path

root_path = Path(__file__).resolve().parent.parent
if str(root_path) not in sys.path:
    sys.path.insert(0, str(root_path))

from app.main import app as fastapi_app


class VercelPathNormalizerMiddleware:
    """Ensure ASGI scope['path'] is normalized for FastAPI router on Vercel."""

    def __init__(self, inner_app):
        self.inner_app = inner_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            original_path = scope.get("path", "")
            # Diagnostic debug endpoint
            if original_path == "/api/__debug__":
                response_body = json.dumps({
                    "scope_path": scope.get("path"),
                    "scope_raw_path": scope.get("raw_path", b"").decode("utf-8", "ignore"),
                    "scope_root_path": scope.get("root_path"),
                    "headers": [
                        [k.decode("utf-8", "ignore"), v.decode("utf-8", "ignore")]
                        for k, v in scope.get("headers", [])
                    ],
                }).encode("utf-8")

                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(response_body)).encode("utf-8")),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": response_body,
                })
                return

            # Strip leading prefix if Vercel routes through /api/index.py or /api
            for prefix in ("/api/index.py", "/api/index", "/api"):
                if original_path.startswith(prefix):
                    remainder = original_path[len(prefix):]
                    scope["path"] = remainder if remainder.startswith("/") else ("/" + remainder if remainder else "/")
                    break

        await self.inner_app(scope, receive, send)


app = VercelPathNormalizerMiddleware(fastapi_app)
