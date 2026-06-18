from __future__ import annotations
import os
import re
import time
import threading
import secrets
import hashlib
import logging
from typing import Optional

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

log = logging.getLogger("security")


_bearer_scheme = HTTPBearer(auto_error=False)


_otp_attempts: dict[str, dict] = {}
_otp_lock = threading.Lock()
OTP_MAX_ATTEMPTS = 5
OTP_WINDOW_SECONDS = 300


_SAFE_REST_ID = re.compile(r'^[a-z0-9_-]{4,60}$')
_SAFE_EMAIL = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
_SAFE_TG_USER = re.compile(r'^[a-zA-Z0-9_]{3,32}$')
_SAFE_ITEM_NAME = re.compile(r'^[a-zA-Z0-9 \-\'\(\)\.]{1,100}$')
_SQL_INJECTION = re.compile(r"('|--|;|/\*|\*/|xp_|0x[0-9a-fA-F]+)", re.IGNORECASE)


class AuthenticatedUser:
    """Populated by require_auth dependency injection."""

    def __init__(self, email: str, restaurant_id: str, token: str):
        self.email = email
        self.restaurant_id = restaurant_id
        self.token = token


def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """
    FastAPI dependency: validates Bearer token, returns AuthenticatedUser.
    Usage:  def my_endpoint(user: AuthenticatedUser = Depends(require_auth)):
    """
    from services import auth

    token = None
    if credentials and credentials.scheme == "Bearer":
        token = credentials.credentials

    if not token:
        token = request.query_params.get("token", "")

    if not token or len(token) < 32:
        raise HTTPException(status_code=401, detail="Authentication required.")

    info = auth.validate_web_token(token)
    if not info:
        raise HTTPException(
            status_code=401, detail="Session expired or invalid. Please log in again."
        )

    return AuthenticatedUser(
        email=info["email"],
        restaurant_id=info["restaurant_id"],
        token=token,
    )


def require_restaurant_access(restaurant_id: str, user: AuthenticatedUser) -> None:
    """
    Verifies the authenticated user owns the target restaurant.
    Prevents horizontal privilege escalation (user A accessing user B's data).
    Call at the start of any endpoint that takes a restaurant_id.
    """
    validate_restaurant_id(restaurant_id)
    if user.restaurant_id != restaurant_id:

        log.warning(
            f"SECURITY: User {user .email } attempted to access restaurant "
            f"{restaurant_id } (owns {user .restaurant_id })"
        )
        raise HTTPException(status_code=403, detail="Access denied.")


def validate_restaurant_id(rid: str) -> str:
    """Validate and return a sanitised restaurant ID."""
    if not rid or not _SAFE_REST_ID.match(rid):
        raise HTTPException(status_code=400, detail="Invalid restaurant ID format.")
    return rid


def validate_email(email: str) -> str:
    """Validate and return lowercased email."""
    if not email or not _SAFE_EMAIL.match(email.strip()):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    if len(email) > 254:
        raise HTTPException(status_code=400, detail="Email too long.")
    return email.strip().lower()


def validate_telegram_username(username: str) -> str:
    """Validate and strip @ from Telegram username."""
    clean = username.lstrip("@").strip() if username else ""
    if not _SAFE_TG_USER.match(clean):
        raise HTTPException(
            status_code=400,
            detail="Invalid Telegram username. Use only letters, numbers, underscore (3-32 chars).",
        )
    return clean.lower()


def validate_item_name(name: str) -> str:
    """Validate a menu item name."""
    if not name or not _SAFE_ITEM_NAME.match(name.strip()):
        raise HTTPException(
            status_code=400,
            detail="Invalid item name. Use only letters, numbers, spaces, hyphens, apostrophes (max 100 chars).",
        )
    return name.strip()


def sanitise_text(text: str, max_len: int = 2000) -> str:
    """
    Sanitise free-text input: strip leading/trailing whitespace,
    truncate to max_len, reject SQL injection patterns.
    """
    if not text:
        return ""
    text = text.strip()[:max_len]
    if _SQL_INJECTION.search(text):
        raise HTTPException(status_code=400, detail="Invalid characters in input.")
    return text


def validate_otp_code(code: str) -> str:
    """Validate OTP is exactly 6 digits."""
    code = code.strip() if code else ""
    if not re.match(r'^\d{6}$', code):
        raise HTTPException(status_code=400, detail="OTP must be exactly 6 digits.")
    return code


def validate_closing_time(ct: str) -> str:
    """Validate HH:MM closing time format."""
    if not ct or not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', ct):
        raise HTTPException(status_code=400, detail="closing_time must be HH:MM (e.g. 21:00).")
    return ct


def check_otp_rate_limit(client_ip: str) -> None:
    """
    Raises HTTP 429 if this IP has exceeded OTP_MAX_ATTEMPTS in OTP_WINDOW_SECONDS.
    Call before processing any OTP verify request.
    """
    now = time.time()
    with _otp_lock:
        entry = _otp_attempts.get(client_ip)
        if entry:

            if now - entry["window_start"] > OTP_WINDOW_SECONDS:
                _otp_attempts[client_ip] = {"attempts": 1, "window_start": now}
                return
            if entry["attempts"] >= OTP_MAX_ATTEMPTS:
                remaining = int(OTP_WINDOW_SECONDS - (now - entry["window_start"]))
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many OTP attempts. Try again in {remaining } seconds.",
                    headers={"Retry-After": str(remaining)},
                )
            entry["attempts"] += 1
        else:
            _otp_attempts[client_ip] = {"attempts": 1, "window_start": now}


def record_failed_otp(client_ip: str) -> None:
    """Increment attempt counter after a failed OTP verification."""
    now = time.time()
    with _otp_lock:
        entry = _otp_attempts.get(client_ip)
        if entry and now - entry["window_start"] <= OTP_WINDOW_SECONDS:
            entry["attempts"] += 1
        else:
            _otp_attempts[client_ip] = {"attempts": 1, "window_start": now}


def clear_otp_attempts(client_ip: str) -> None:
    """Clear brute-force counter after a successful OTP (reward success)."""
    with _otp_lock:
        _otp_attempts.pop(client_ip, None)


def get_client_ip(request: Request) -> str:
    """Extract real client IP, accounting for reverse proxies.

    Security note: X-Forwarded-For is attacker-controlled and can be spoofed
    when the app is not behind a trusted proxy. We take the LAST IP in the
    chain (added by our own proxy/load-balancer) rather than the first
    (which the attacker supplies). If not behind a proxy, falls back to the
    direct connection IP.
    """
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        # The last entry is the one our trusted reverse-proxy appended.
        # It cannot be forged by the client.
        ips = [ip.strip() for ip in forwarded.split(",") if ip.strip()]
        if ips:
            return ips[-1]
    return request.client.host if request.client else "unknown"



def secure_token(nbytes: int = 32) -> str:
    """Generate a cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(nbytes)


def secure_otp() -> str:
    """Generate a cryptographically secure 6-digit OTP."""

    return str(secrets.randbelow(900000) + 100000)


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds production-grade security headers to every response.
    Prevents: clickjacking, MIME-sniffing, XSS, data leakage.
    """

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"

        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
        return response



def verify_telegram_webhook(token: str, expected_token: str) -> bool:
    """
    Constant-time comparison of Telegram webhook secret.
    Prevents timing attacks on webhook token comparison.
    """
    return secrets.compare_digest(token.encode(), expected_token.encode())

