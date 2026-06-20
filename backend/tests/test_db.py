def test_request_lifecycle(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    from app import db

    db.init()
    row = db.upsert_request(
        type="album", foreign_id="mbid-1", title="OK Computer", artist="Radiohead",
        image_url=None, lidarr_artist_id=1, lidarr_album_id=2, status="pending",
    )
    assert row["status"] == "pending"
    assert db.list_requests()[0]["foreign_id"] == "mbid-1"

    db.update_status(row["id"], "available")
    assert db.get_request(row["id"])["status"] == "available"


def test_search_attempts_and_active_ids(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test2.db"))
    from app import db

    db.init()
    row = db.upsert_request(
        type="album", foreign_id="mbid-2", title="In Rainbows", artist="Radiohead",
        image_url=None, lidarr_artist_id=1, lidarr_album_id=3, status="pending",
    )
    db.bump_search_attempt(row["id"], 3)
    assert db.get_request(row["id"])["search_attempts"] == 3
    db.reset_search_attempts(row["id"])
    assert db.get_request(row["id"])["search_attempts"] == 0

    # 'failed'/'error' are excluded from the UI request-lock set.
    assert "mbid-2" in db.active_request_foreign_ids()
    db.update_status(row["id"], "failed")
    assert "mbid-2" not in db.active_request_foreign_ids()
