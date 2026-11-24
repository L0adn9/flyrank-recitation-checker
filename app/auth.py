"""Email + password accounts. Passwords are hashed with argon2; sessions are short-lived JWT bearer tokens."""
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.db import connection

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)
# Verified against when the email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash("not-a-real-password")
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except VerificationError:
        return False


def issue_token(user_id: int) -> tuple[str, int]:
    ttl = settings.jwt_ttl_minutes * 60
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(seconds=ttl)}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM), ttl


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(401, message, headers={"WWW-Authenticate": "Bearer"})


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    """Dependency for protected routes: a valid, unexpired token for a user that still exists."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized("Sign in first: send 'Authorization: Bearer <token>'")
    try:
        claims = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=[ALGORITHM],
                            options={"require": ["exp", "sub"]})
        user_id = int(claims["sub"])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Your session expired. Please sign in again") from None
    except (jwt.InvalidTokenError, ValueError):
        raise _unauthorized("Invalid token") from None
    with connection() as conn:
        user = conn.execute("SELECT id, email, name, created_at FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None:
        raise _unauthorized("This account no longer exists")
    return user
