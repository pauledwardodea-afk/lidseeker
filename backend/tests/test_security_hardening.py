"""Round-2 hardening: JWT secret auto-generation/persistence, login brute-force
limiter, and leak-free atomic Soularr config writes."""
import asyncio
import json
import stat

import pytest
from fastapi import HTTPException

from app import config, main, soularr_cfg
from app.schemas import LoginIn


# --- JWT secret auto-generation ---------------------------------------------

def test_jwt_secret_generated_and_persisted(tmp_path, monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "lidseeker.db"))
    secret1 = config._resolve_jwt_secret()
    assert secret1 and secret1 != "change-me"
    secret_file = tmp_path / ".jwt_secret"
    assert secret_file.exists()
    # 0600 — owner read/write only.
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    # A second resolve reuses the persisted secret (restarts don't sign users out).
    assert config._resolve_jwt_secret() == secret1


def test_jwt_secret_respects_explicit_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "a-real-operator-secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "lidseeker.db"))
    assert config._resolve_jwt_secret() == "a-real-operator-secret"
    # Nothing persisted when the operator set one.
    assert not (tmp_path / ".jwt_secret").exists()


def test_jwt_secret_treats_placeholder_as_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "change-me")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "lidseeker.db"))
    assert config._resolve_jwt_secret() != "change-me"


# --- Login brute-force limiter ----------------------------------------------

class _Req:
    def __init__(self, ip="1.2.3.4"):
        self.client = type("C", (), {"host": ip})()


def _reset_limiter():
    main._login_failures.clear()


def test_login_limiter_blocks_after_threshold(monkeypatch):
    _reset_limiter()
    monkeypatch.setattr(main.auth, "verify_credentials", lambda u, p: None)
    req = _Req()
    body = LoginIn(username="x", password="wrong")
    # First N failures → 401.
    for _ in range(main._LOGIN_MAX_FAILURES):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(main.login(body, req))
        assert ei.value.status_code == 401
    # Next one → 429 (blocked).
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.login(body, req))
    assert ei.value.status_code == 429


def test_login_limiter_isolated_per_ip(monkeypatch):
    _reset_limiter()
    monkeypatch.setattr(main.auth, "verify_credentials", lambda u, p: None)
    body = LoginIn(username="x", password="wrong")
    for _ in range(main._LOGIN_MAX_FAILURES):
        with pytest.raises(HTTPException):
            asyncio.run(main.login(body, _Req("1.1.1.1")))
    # A different IP is unaffected (still 401, not 429).
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.login(body, _Req("2.2.2.2")))
    assert ei.value.status_code == 401


def test_login_success_resets_counter(monkeypatch):
    _reset_limiter()
    req = _Req("3.3.3.3")
    body = LoginIn(username="x", password="pw")
    # A few failures, then a success clears the counter.
    monkeypatch.setattr(main.auth, "verify_credentials", lambda u, p: None)
    for _ in range(main._LOGIN_MAX_FAILURES - 1):
        with pytest.raises(HTTPException):
            asyncio.run(main.login(body, req))
    monkeypatch.setattr(main.auth, "verify_credentials", lambda u, p: {"id": 1})
    monkeypatch.setattr(main.auth, "issue_token", lambda user: "tok")
    out = asyncio.run(main.login(body, req))
    assert out.token == "tok"
    assert "3.3.3.3" not in main._login_failures


# --- Soularr config: atomic, leak-free writes -------------------------------

def test_clear_denylist_entry_atomic(tmp_path, monkeypatch):
    denylist = tmp_path / "failed_imports.json"
    denylist.write_text(json.dumps({"42": "boom", "99": "nope"}))
    monkeypatch.setattr(config, "SOULARR_DENYLIST_PATH", str(denylist))
    assert soularr_cfg.clear_denylist_entry(42) is True
    # File is still valid JSON and the entry is gone (no half-write/corruption).
    data = json.loads(denylist.read_text())
    assert "42" not in data and "99" in data
    # No leftover temp files in the dir.
    assert [p.name for p in tmp_path.iterdir()] == ["failed_imports.json"]
    # Removing a missing key is a no-op.
    assert soularr_cfg.clear_denylist_entry(12345) is False


def test_is_denylisted_reads_cleanly(tmp_path, monkeypatch):
    denylist = tmp_path / "failed_imports.json"
    denylist.write_text(json.dumps({"7": "x"}))
    monkeypatch.setattr(config, "SOULARR_ENABLED", True)
    monkeypatch.setattr(config, "SOULARR_DENYLIST_PATH", str(denylist))
    assert soularr_cfg.is_denylisted(7) is True
    assert soularr_cfg.is_denylisted(8) is False
