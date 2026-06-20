"""Authentication: bcrypt login -> HS256 JWT carrying the user's id + role."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config, db

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: int
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def verify_credentials(username: str, password: str) -> dict | None:
    """Return the user row if the password matches, else None."""
    user = db.get_user(username)
    if not user:
        return None
    try:
        if bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return user
    except ValueError:
        return None
    return None


def issue_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["username"],
        "uid": user["id"],
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(hours=config.JWT_TTL_HOURS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def require_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> CurrentUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    try:
        payload = jwt.decode(creds.credentials, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None
    return CurrentUser(
        id=payload.get("uid"),
        username=payload["sub"],
        role=payload.get("role", "user"),
    )


def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user
