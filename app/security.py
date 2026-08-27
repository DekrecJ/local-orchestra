from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.settings import settings


IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


def api_error(
    status_code: int,
    code: str,
    message: str,
    category: str,
    *,
    retryable: bool = False,
    details: dict | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
            "category": category,
            "retryable": retryable,
            "details": details or {},
        },
    )


def read_api_token() -> str | None:
    try:
        token = settings.api_token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


async def require_authentication(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        HTTPBearer(auto_error=False)
    ),
) -> str:
    if not settings.api_auth_enabled:
        return "local-auth-disabled"
    expected = read_api_token()
    if expected is None:
        raise api_error(
            503,
            "authentication_unconfigured",
            "La autenticación está cerrada hasta configurar el token local.",
            "infrastructure",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise api_error(401, "authentication_required", "Se requiere Bearer token.", "authentication")
    supplied = credentials.credentials.strip()
    if not hmac.compare_digest(supplied, expected):
        raise api_error(403, "authentication_failed", "Credencial no válida.", "authorization")
    return hashlib.sha256(expected.encode()).hexdigest()[:16]


@dataclass
class SlidingWindowRateLimiter:
    requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        self._entries: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        with self._lock:
            entries = self._entries[key]
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= self.requests:
                return False
            entries.append(current)
            return True


rate_limiter = SlidingWindowRateLimiter(
    settings.api_rate_limit_requests,
    settings.api_rate_limit_window_seconds,
)


async def enforce_rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(client):
        raise api_error(429, "rate_limit_exceeded", "Límite de solicitudes excedido.", "rate_limit", retryable=True)


def validate_idempotency_key(value: str | None) -> str:
    if value is None or not IDEMPOTENCY_PATTERN.fullmatch(value):
        raise api_error(
            422,
            "invalid_idempotency_key",
            "Idempotency-Key debe tener entre 8 y 128 caracteres seguros.",
            "validation",
        )
    return value


def workflow_id_for(subject: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{subject}:{idempotency_key}".encode()).hexdigest()[:40]
    return f"job-{digest}"
