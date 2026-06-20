from datetime import datetime, timedelta, timezone

from app import main


def test_seconds_elapsed():
    old = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    assert main._seconds_elapsed(old, 300) is True
    assert main._seconds_elapsed(recent, 300) is False
    # Unparseable timestamps are treated as "elapsed" so a request can't get stuck.
    assert main._seconds_elapsed("garbage", 300) is True
