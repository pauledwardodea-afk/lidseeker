import bcrypt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app import auth, config


def test_soularr_autodetect(monkeypatch):
    monkeypatch.setattr(config, "SLSKD_API_KEY", "")
    assert config._soularr_autodetect() is False

    monkeypatch.setattr(config, "SLSKD_API_KEY", "a-key")
    monkeypatch.setattr(config, "SOULARR_CONFIG_PATH", "/definitely/not/here.ini")
    assert config._soularr_autodetect() is False  # key set but config missing


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setattr(config, "APP_PASS_HASH", "")  # don't seed an env admin
    from app import db

    db.init()
    return db


def test_verify_credentials(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    db.create_user("alice", bcrypt.hashpw(b"hunter2", bcrypt.gensalt()).decode(), "user")
    assert auth.verify_credentials("alice", "hunter2")["username"] == "alice"
    assert auth.verify_credentials("alice", "wrong") is None
    assert auth.verify_credentials("nobody", "hunter2") is None


def test_token_roundtrip(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "JWT_SECRET", "test-secret")
    token = auth.issue_token({"username": "alice", "id": 7, "role": "admin"})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    cu = auth.require_user(creds)
    assert cu.username == "alice"
    assert cu.id == 7
    assert cu.is_admin


def test_require_admin():
    admin = auth.CurrentUser(id=1, username="a", role="admin")
    user = auth.CurrentUser(id=2, username="b", role="user")
    assert auth.require_admin(admin) is admin
    with pytest.raises(HTTPException):
        auth.require_admin(user)
