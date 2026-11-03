from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.security.csrf import csrf_token, verify_csrf


class RequestStub:
    def __init__(self) -> None:
        self.session: dict[str, str] = {}


def test_csrf_token_round_trip() -> None:
    request = RequestStub()
    token = csrf_token(request)  # type: ignore[arg-type]
    verify_csrf(request, token)  # type: ignore[arg-type]
    assert csrf_token(request) == token  # type: ignore[arg-type]


def test_csrf_rejects_invalid_token() -> None:
    request = RequestStub()
    csrf_token(request)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        verify_csrf(request, "wrong")  # type: ignore[arg-type]
    assert exc.value.status_code == 403