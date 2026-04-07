"""Tests for the authentication modules: JWT, rate limiter, middleware."""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.auth.jwt_handler import create_token, verify_token
from src.auth.rate_limiter import RateLimiter


# ── JWT Handler Tests ────────────────────────────────────────────────


class TestJWTHandler:
    def test_create_and_verify(self):
        token = create_token("testuser")
        claims = verify_token(token)
        assert claims is not None
        assert claims["sub"] == "testuser"

    def test_extra_claims(self):
        token = create_token("testuser", extra_claims={"role": "admin"})
        claims = verify_token(token)
        assert claims["role"] == "admin"
        assert claims["sub"] == "testuser"

    def test_invalid_token(self):
        assert verify_token("not-a-real-token") is None

    def test_tampered_token(self):
        token = create_token("testuser")
        # Tamper with the token
        tampered = token[:-5] + "XXXXX"
        assert verify_token(tampered) is None

    def test_expired_token(self):
        with patch("src.auth.jwt_handler.EXPIRY_HOURS", 0):
            # Create token with 0-hour expiry (immediately expired)
            from datetime import datetime, timedelta, timezone

            from jose import jwt as jose_jwt

            from src.auth.jwt_handler import ALGORITHM, SECRET_KEY

            now = datetime.now(timezone.utc)
            payload = {
                "sub": "testuser",
                "iat": now - timedelta(hours=2),
                "exp": now - timedelta(hours=1),
            }
            token = jose_jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
            assert verify_token(token) is None

    def test_different_users_different_tokens(self):
        t1 = create_token("alice")
        t2 = create_token("bob")
        assert t1 != t2
        assert verify_token(t1)["sub"] == "alice"
        assert verify_token(t2)["sub"] == "bob"


# ── Rate Limiter Tests ───────────────────────────────────────────────


class TestRateLimiter:
    def test_allow_within_limit(self):
        rl = RateLimiter(max_attempts=3, lockout_seconds=60)
        allowed, _ = rl.is_allowed("test-ip")
        assert allowed is True

    def test_lockout_after_max_failures(self):
        rl = RateLimiter(max_attempts=3, lockout_seconds=60)
        rl.record_failure("test-ip")
        rl.record_failure("test-ip")
        locked, _ = rl.record_failure("test-ip")
        assert locked is True

        allowed, wait = rl.is_allowed("test-ip")
        assert allowed is False
        assert wait > 0

    def test_different_keys_independent(self):
        rl = RateLimiter(max_attempts=2, lockout_seconds=60)
        rl.record_failure("ip-a")
        rl.record_failure("ip-a")
        rl.record_failure("ip-a")

        # ip-b should still be allowed
        allowed, _ = rl.is_allowed("ip-b")
        assert allowed is True

    def test_success_clears_tracking(self):
        rl = RateLimiter(max_attempts=3, lockout_seconds=60)
        rl.record_failure("test-ip")
        rl.record_failure("test-ip")
        rl.record_success("test-ip")

        # Should be reset after success
        allowed, _ = rl.is_allowed("test-ip")
        assert allowed is True

    def test_cleanup_removes_stale(self):
        rl = RateLimiter(max_attempts=3, lockout_seconds=1)
        rl.record_failure("old-ip")

        # Wait for it to become stale
        time.sleep(0.1)
        rl.cleanup(max_age=0.05)

        # Internal tracking should be cleaned
        assert "old-ip" not in rl._attempts

    def test_active_lockouts_count(self):
        rl = RateLimiter(max_attempts=2, lockout_seconds=60)
        assert rl.active_lockouts == 0

        rl.record_failure("ip-a")
        rl.record_failure("ip-a")
        assert rl.active_lockouts == 1

        rl.record_failure("ip-b")
        rl.record_failure("ip-b")
        assert rl.active_lockouts == 2

    def test_lockout_expires(self):
        rl = RateLimiter(max_attempts=2, lockout_seconds=1)
        rl.record_failure("test-ip")
        rl.record_failure("test-ip")

        allowed, _ = rl.is_allowed("test-ip")
        assert allowed is False

        # Wait for lockout to expire
        time.sleep(1.1)
        allowed, _ = rl.is_allowed("test-ip")
        assert allowed is True


# ── Middleware Tests ─────────────────────────────────────────────────


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_require_auth_disabled(self):
        """When auth is disabled, require_auth passes through."""
        with patch("src.auth.middleware.AUTH_ENABLED", False):
            from src.auth.middleware import require_auth

            mock_request = MagicMock()
            result = await require_auth(mock_request)
            assert result["sub"] == "local"
            assert result["auth"] == "disabled"

    @pytest.mark.asyncio
    async def test_require_auth_no_cookie(self):
        """When auth is enabled and no cookie, raises 401."""
        with patch("src.auth.middleware.AUTH_ENABLED", True):
            from fastapi import HTTPException

            from src.auth.middleware import require_auth

            mock_request = MagicMock()
            mock_request.cookies = {}

            with pytest.raises(HTTPException) as exc_info:
                await require_auth(mock_request)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_require_auth_valid_cookie(self):
        """When auth is enabled and valid JWT cookie, returns claims."""
        token = create_token("testuser")
        with patch("src.auth.middleware.AUTH_ENABLED", True):
            from src.auth.middleware import COOKIE_NAME, require_auth

            mock_request = MagicMock()
            mock_request.cookies = {COOKIE_NAME: token}

            result = await require_auth(mock_request)
            assert result["sub"] == "testuser"

    @pytest.mark.asyncio
    async def test_require_auth_expired_cookie(self):
        """When auth is enabled and expired JWT, raises 401."""
        from datetime import datetime, timedelta, timezone

        from jose import jwt as jose_jwt

        from src.auth.jwt_handler import ALGORITHM, SECRET_KEY

        now = datetime.now(timezone.utc)
        payload = {
            "sub": "testuser",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        }
        token = jose_jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        with patch("src.auth.middleware.AUTH_ENABLED", True):
            from fastapi import HTTPException

            from src.auth.middleware import COOKIE_NAME, require_auth

            mock_request = MagicMock()
            mock_request.cookies = {COOKIE_NAME: token}

            with pytest.raises(HTTPException) as exc_info:
                await require_auth(mock_request)
            assert exc_info.value.status_code == 401

    def test_attempt_login_rate_limited_ip(self):
        """IP rate limiting blocks after max attempts."""
        with (
            patch("src.auth.middleware.AUTH_ENABLED", True),
            patch("src.auth.middleware.ip_limiter") as mock_ip,
            patch("src.auth.middleware.user_limiter") as mock_user,
        ):
            mock_ip.is_allowed.return_value = (False, 300)
            mock_user.is_allowed.return_value = (True, 0)

            from src.auth.middleware import attempt_login

            success, message = attempt_login("user", "pass", "1.2.3.4")
            assert success is False
            assert "Too many attempts" in message

    def test_attempt_login_rate_limited_user(self):
        """Username rate limiting blocks after max attempts."""
        with (
            patch("src.auth.middleware.AUTH_ENABLED", True),
            patch("src.auth.middleware.ip_limiter") as mock_ip,
            patch("src.auth.middleware.user_limiter") as mock_user,
        ):
            mock_ip.is_allowed.return_value = (True, 0)
            mock_user.is_allowed.return_value = (False, 600)

            from src.auth.middleware import attempt_login

            success, message = attempt_login("user", "pass", "1.2.3.4")
            assert success is False
            assert "locked" in message.lower()

    def test_get_client_ip_direct(self):
        """get_client_ip returns client host when no proxy headers."""
        from src.auth.middleware import get_client_ip

        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.client.host = "192.168.1.1"

        assert get_client_ip(mock_request) == "192.168.1.1"

    def test_get_client_ip_forwarded(self):
        """get_client_ip returns X-Forwarded-For when present."""
        from src.auth.middleware import get_client_ip

        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "10.0.0.1, 192.168.1.1"}
        mock_request.client.host = "172.17.0.1"

        assert get_client_ip(mock_request) == "10.0.0.1"
