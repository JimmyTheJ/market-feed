"""FastAPI authentication middleware and dependencies.

Provides:
- require_auth: FastAPI dependency that protects routes
- authenticate_user: validates credentials with rate limiting
- auth_log: fail2ban-compatible event logging
- Login/logout/status route handlers
"""

import logging
import os
from typing import Optional

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

from .jwt_handler import create_token, verify_token
from .ldap_auth import LDAPAuthenticator
from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "mp_session")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

# ── Rate limiters ────────────────────────────────────────────────────

ip_limiter = RateLimiter(
    max_attempts=int(os.getenv("MAX_LOGIN_ATTEMPTS_PER_IP", "5")),
    lockout_seconds=int(os.getenv("IP_LOCKOUT_MINUTES", "15")) * 60,
)

user_limiter = RateLimiter(
    max_attempts=int(os.getenv("MAX_LOGIN_ATTEMPTS_PER_USER", "10")),
    lockout_seconds=int(os.getenv("USER_LOCKOUT_MINUTES", "30")) * 60,
)

# ── LDAP client (lazy) ──────────────────────────────────────────────

_ldap_auth: Optional[LDAPAuthenticator] = None


def get_ldap_auth() -> LDAPAuthenticator:
    global _ldap_auth
    if _ldap_auth is None:
        _ldap_auth = LDAPAuthenticator()
    return _ldap_auth


# ── Helpers ──────────────────────────────────────────────────────────


def get_client_ip(request: Request) -> str:
    """Get client IP, respecting X-Forwarded-For from reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def auth_log(event: str, ip: str, username: str):
    """Log auth events in fail2ban-compatible format."""
    logger.info(f"AUTH_{event} ip={ip} username={username}")


# ── FastAPI dependency ───────────────────────────────────────────────


async def require_auth(request: Request) -> dict:
    """FastAPI dependency: require authentication.

    Returns JWT claims dict if authenticated.
    Raises HTTPException(401) if not.
    Passes through if AUTH_ENABLED=false.
    """
    if not AUTH_ENABLED:
        return {"sub": "local", "auth": "disabled"}

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    claims = verify_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return claims


# ── Auth operations ──────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


def attempt_login(
    username: str, password: str, client_ip: str
) -> tuple[bool, str]:
    """Authenticate user with rate limiting.

    Returns (success, message).
    Always returns generic error messages to avoid leaking information.
    """
    # Check IP rate limit
    ip_allowed, ip_wait = ip_limiter.is_allowed(client_ip)
    if not ip_allowed:
        auth_log("LOCKOUT", client_ip, username)
        return False, f"Too many attempts. Try again in {ip_wait} seconds."

    # Check username rate limit
    user_allowed, user_wait = user_limiter.is_allowed(username.lower())
    if not user_allowed:
        auth_log("LOCKOUT", client_ip, username)
        return False, f"Account temporarily locked. Try again in {user_wait} seconds."

    # Authenticate via LDAP
    ldap = get_ldap_auth()
    success, _ldap_msg = ldap.authenticate(username, password)

    if success:
        ip_limiter.record_success(client_ip)
        user_limiter.record_success(username.lower())
        auth_log("SUCCESS", client_ip, username)
        return True, "Authenticated"
    else:
        ip_limiter.record_failure(client_ip)
        user_limiter.record_failure(username.lower())
        auth_log("FAILURE", client_ip, username)
        # Always return generic message
        return False, "Invalid credentials"


def create_auth_cookie(response: Response, username: str) -> str:
    """Create JWT and set it as httpOnly cookie on the response."""
    token = create_token(username)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=int(os.getenv("JWT_EXPIRY_HOURS", "24")) * 3600,
        path="/",
    )
    return token


def clear_auth_cookie(response: Response):
    """Clear the auth cookie."""
    response.delete_cookie(key=COOKIE_NAME, path="/")
