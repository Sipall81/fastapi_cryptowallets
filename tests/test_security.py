from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt

from auth.jwt import create_access_token, create_refresh_token, decode_token
from auth.password import hash_password, verify_password
from config import settings


class TestPasswordHashing:

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("password123")
        assert hashed != "password123"
        assert hashed.startswith("$2")  # bcrypt

    def test_verify_correct_password(self):
        assert verify_password("password123", hash_password("password123")) is True

    def test_verify_wrong_password(self):
        assert verify_password("wrong-password", hash_password("password123")) is False

    def test_hashes_are_salted(self):
        assert hash_password("password123") != hash_password("password123")

    def test_unicode_password(self):
        hashed = hash_password("пароль-🔑-123")
        assert verify_password("пароль-🔑-123", hashed) is True
        assert verify_password("пароль-🔑-124", hashed) is False


class TestAccessToken:

    def test_payload(self):
        payload = decode_token(create_access_token(42, "admin"))
        assert payload["sub"] == "42"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_default_role_is_user(self):
        assert decode_token(create_access_token(1))["role"] == "user"

    def test_expires_in_about_15_minutes(self):
        payload = decode_token(create_access_token(1))
        expected = datetime.now(timezone.utc) + timedelta(minutes=15)
        assert payload["exp"] == pytest.approx(expected.timestamp(), abs=60)


class TestRefreshToken:

    def test_payload(self):
        payload = decode_token(create_refresh_token(42))
        assert payload["sub"] == "42"
        assert payload["type"] == "refresh"
        assert "role" not in payload

    def test_expires_after_configured_days(self):
        payload = decode_token(create_refresh_token(1))
        expected = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
        assert payload["exp"] == pytest.approx(expected.timestamp(), abs=60)


class TestDecodeToken:

    def test_garbage_token(self):
        with pytest.raises(HTTPException) as exc:
            decode_token("definitely-not-a-jwt")
        assert exc.value.status_code == 401
        assert exc.value.detail.startswith("Invalid token")
        assert exc.value.headers == {"WWW-Authenticate": "Bearer"}

    def test_expired_token(self):
        expired = jwt.encode(
            {"sub": "1", "type": "access", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        with pytest.raises(HTTPException) as exc:
            decode_token(expired)
        assert exc.value.status_code == 401

    def test_wrong_signature(self):
        forged = jwt.encode(
            {"sub": "1", "type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "another-secret",
            algorithm=settings.algorithm,
        )
        with pytest.raises(HTTPException) as exc:
            decode_token(forged)
        assert exc.value.status_code == 401

    def test_tampered_payload(self):
        token = create_access_token(1, "user")
        header, _payload, signature = token.split(".")
        forged_payload = jwt.encode(
            {"sub": "1", "role": "admin", "type": "access", "exp": 9999999999},
            "another-secret",
            algorithm=settings.algorithm,
        ).split(".")[1]

        with pytest.raises(HTTPException) as exc:
            decode_token(f"{header}.{forged_payload}.{signature}")
        assert exc.value.status_code == 401
