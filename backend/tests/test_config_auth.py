import bcrypt
from fastapi.security import HTTPAuthorizationCredentials

from app import auth, config


def test_soularr_autodetect(monkeypatch):
    monkeypatch.setattr(config, "SLSKD_API_KEY", "")
    assert config._soularr_autodetect() is False

    monkeypatch.setattr(config, "SLSKD_API_KEY", "a-key")
    monkeypatch.setattr(config, "SOULARR_CONFIG_PATH", "/definitely/not/here.ini")
    assert config._soularr_autodetect() is False  # key set but config missing


def test_verify_credentials(monkeypatch):
    h = bcrypt.hashpw(b"hunter2", bcrypt.gensalt()).decode()
    monkeypatch.setattr(config, "APP_USER", "admin")
    monkeypatch.setattr(config, "APP_PASS_HASH", h)
    assert auth.verify_credentials("admin", "hunter2") is True
    assert auth.verify_credentials("admin", "wrong") is False
    assert auth.verify_credentials("someone", "hunter2") is False


def test_token_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "JWT_SECRET", "test-secret")
    token = auth.issue_token("admin")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert auth.require_user(creds) == "admin"
