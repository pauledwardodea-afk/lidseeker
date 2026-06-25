"""Tests for the hardening added for public exposure: login rate-limiting,
proxy-aware client IP, and the security response headers."""
from fastapi.testclient import TestClient

from app import config
from app.ratelimit import SlidingWindowLimiter, client_ip


def test_sliding_window_blocks_at_cap():
    lim = SlidingWindowLimiter(max_events=2, window_seconds=300)
    assert lim.is_blocked("1.2.3.4") is False
    lim.register("1.2.3.4")
    assert lim.is_blocked("1.2.3.4") is False
    lim.register("1.2.3.4")
    assert lim.is_blocked("1.2.3.4") is True
    # A different key has its own independent budget.
    assert lim.is_blocked("5.6.7.8") is False


class _FakeRequest:
    def __init__(self, host: str, headers: dict):
        self.client = type("C", (), {"host": host})()
        self.headers = headers


def test_client_ip_respects_trust_proxy(monkeypatch):
    req = _FakeRequest("10.0.0.1", {"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
    monkeypatch.setattr(config, "TRUST_PROXY", False)
    assert client_ip(req) == "10.0.0.1"           # XFF ignored — could be spoofed
    monkeypatch.setattr(config, "TRUST_PROXY", True)
    assert client_ip(req) == "203.0.113.9"        # left-most forwarded client


def test_security_headers_and_login_rate_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "sec.db"))
    from app import main
    monkeypatch.setattr(main, "_LOGIN_MAX_FAILURES", 2)
    main._login_failures.clear()

    with TestClient(main.app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"

        bad = {"username": "nobody", "password": "wrong"}
        # Two failed attempts are allowed, the third is throttled.
        assert client.post("/api/auth/login", json=bad).status_code == 401
        assert client.post("/api/auth/login", json=bad).status_code == 401
        assert client.post("/api/auth/login", json=bad).status_code == 429
