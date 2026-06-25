"""Round-2 hardening: JWT secret auto-generation/persistence, login brute-force
limiter, and leak-free atomic Soularr config writes."""
import asyncio
import json
import stat
import time as _time

import pytest
from fastapi import HTTPException

from app import config, main, ratelimit, soularr_cfg
from app.ratelimit import SlidingWindowLimiter
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
    """Minimal request stub: client.host for IP, empty headers (no proxy)."""
    def __init__(self, ip: str = "1.2.3.4"):
        self.client = type("C", (), {"host": ip})()
        self.headers: dict[str, str] = {}


def _make_tight_limiter(monkeypatch, max_events: int = 2) -> SlidingWindowLimiter:
    limiter = SlidingWindowLimiter(max_events=max_events, window_seconds=60)
    monkeypatch.setattr(ratelimit, "login_limiter", limiter)
    monkeypatch.setattr(main, "login_limiter", limiter)
    return limiter


def test_login_limiter_blocks_after_threshold(monkeypatch):
    limiter = _make_tight_limiter(monkeypatch, max_events=2)
    monkeypatch.setattr(main.auth, "verify_credentials", lambda u, p: None)
    req = _Req()
    body = LoginIn(username="x", password="wrong")
    # First max_events failures → 401.
    for _ in range(limiter.max_events):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(main.login(body, req))
        assert ei.value.status_code == 401
    # Next one → 429 (blocked).
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.login(body, req))
    assert ei.value.status_code == 429


def test_login_limiter_isolated_per_ip(monkeypatch):
    limiter = _make_tight_limiter(monkeypatch, max_events=2)
    monkeypatch.setattr(main.auth, "verify_credentials", lambda u, p: None)
    body = LoginIn(username="x", password="wrong")
    # Exhaust IP 1.1.1.1.
    for _ in range(limiter.max_events):
        with pytest.raises(HTTPException):
            asyncio.run(main.login(body, _Req("1.1.1.1")))
    # A different IP is unaffected (still 401, not 429).
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.login(body, _Req("2.2.2.2")))
    assert ei.value.status_code == 401


def test_login_success_resets_counter(monkeypatch):
    limiter = _make_tight_limiter(monkeypatch, max_events=3)
    req = _Req("3.3.3.3")
    body = LoginIn(username="x", password="pw")
    # A few failures, then a success clears the counter.
    monkeypatch.setattr(main.auth, "verify_credentials", lambda u, p: None)
    for _ in range(limiter.max_events - 1):
        with pytest.raises(HTTPException):
            asyncio.run(main.login(body, req))
    monkeypatch.setattr(main.auth, "verify_credentials", lambda u, p: {"id": 1})
    monkeypatch.setattr(main.auth, "issue_token", lambda user: "tok")
    out = asyncio.run(main.login(body, req))
    assert out.token == "tok"
    assert not limiter.is_blocked("3.3.3.3")
    assert len(limiter._hits.get("3.3.3.3", [])) == 0


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


# --- SlidingWindowLimiter unit tests ----------------------------------------

def test_rate_limiter_allows_up_to_limit():
    rl = SlidingWindowLimiter(max_events=5, window_seconds=60)
    for _ in range(5):
        assert not rl.is_blocked("5.5.5.5")
        rl.register("5.5.5.5")
    assert rl.is_blocked("5.5.5.5")


def test_rate_limiter_isolated_per_ip():
    rl = SlidingWindowLimiter(max_events=3, window_seconds=60)
    for _ in range(3):
        rl.register("a.a.a.a")
    assert rl.is_blocked("a.a.a.a")
    assert not rl.is_blocked("b.b.b.b")


def test_rate_limiter_clear_resets_key():
    rl = SlidingWindowLimiter(max_events=2, window_seconds=60)
    rl.register("x.x.x.x")
    rl.register("x.x.x.x")
    assert rl.is_blocked("x.x.x.x")
    rl.clear("x.x.x.x")
    assert not rl.is_blocked("x.x.x.x")


def test_rate_limiter_prunes_stale_entries():
    rl = SlidingWindowLimiter(max_events=3, window_seconds=60)
    # Insert stale timestamps directly.
    stale = _time.monotonic() - 120
    rl._hits["dead.ip"].append(stale)
    rl._hits["alive.ip"].append(_time.monotonic())
    # is_blocked prunes internally via _prune.
    assert not rl.is_blocked("dead.ip")
    assert len(rl._hits["dead.ip"]) == 0
    assert len(rl._hits["alive.ip"]) == 1


def test_rate_limiter_window_expiry_unblocks():
    rl = SlidingWindowLimiter(max_events=2, window_seconds=1)
    rl.register("z.z.z.z")
    rl.register("z.z.z.z")
    assert rl.is_blocked("z.z.z.z")
    # Manually age the timestamps past the window.
    for i, _ in enumerate(rl._hits["z.z.z.z"]):
        rl._hits["z.z.z.z"][i] = _time.monotonic() - 2
    assert not rl.is_blocked("z.z.z.z")
