"""JWT token management for session authentication.

Tokens are stored as httpOnly secure cookies — not accessible via JavaScript,
preventing XSS-based token theft.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

# Generate a random secret if none provided. In production, set JWT_SECRET
# in .env so tokens survive container restarts.
SECRET_KEY = os.getenv("JWT_SECRET", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(32)

ALGORITHM = "HS256"
EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "24"))


def create_token(username: str, extra_claims: dict | None = None) -> str:
    """Create a JWT token for a user."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + timedelta(hours=EXPIRY_HOURS),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """Verify a JWT token and return claims, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
