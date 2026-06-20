from datetime import datetime, timedelta, timezone

from app import lidarr


def test_status_from_percent():
    assert lidarr._status_from_percent(None) == "pending"
    assert lidarr._status_from_percent(0) == "pending"
    assert lidarr._status_from_percent(42) == "downloading"
    assert lidarr._status_from_percent(99.9) == "downloading"
    assert lidarr._status_from_percent(100) == "available"


def test_build_pipeline_shape():
    p = lidarr._build_pipeline("searching", 0, 0, 10, "Searching…")
    assert p["stage"] == "searching"
    assert p["stageIndex"] == lidarr.PIPELINE_STAGES.index("searching")
    assert p["stages"] == lidarr.PIPELINE_STAGES
    assert p["trackCount"] == 10
    assert p["failed"] is False
    assert p["stuck"] is False


def test_build_pipeline_failed_flag():
    p = lidarr._build_pipeline("searching", 0, 0, 0, "No source", failed=True)
    assert p["failed"] is True


def test_is_stuck():
    old = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    assert lidarr._is_stuck(old) is True
    assert lidarr._is_stuck(recent) is False
    assert lidarr._is_stuck(None) is False
    assert lidarr._is_stuck("not-a-date") is False
