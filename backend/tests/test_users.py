import bcrypt

from app import config


def _db(tmp_path, monkeypatch, seed_hash=""):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "u.db"))
    monkeypatch.setattr(config, "APP_USER", "admin")
    monkeypatch.setattr(config, "APP_PASS_HASH", seed_hash)
    from app import db

    db.init()
    return db


def test_seed_admin_from_env(tmp_path, monkeypatch):
    h = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    db = _db(tmp_path, monkeypatch, seed_hash=h)
    users = db.list_users()
    assert len(users) == 1
    assert users[0]["username"] == "admin"
    assert users[0]["role"] == "admin"


def test_user_crud_and_helpers(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)  # no env seed (empty hash)
    assert db.list_users() == []
    admin = db.create_user("admin", "h", "admin")
    bob = db.create_user("bob", "h", "user")
    assert {u["username"] for u in db.list_users()} == {"admin", "bob"}
    assert db.count_admins() == 1
    assert db.usernames_by_id()[admin["id"]] == "admin"
    assert db.delete_user(bob["id"]) is True
    assert db.get_user("bob") is None


def test_request_attribution_and_visibility(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    bob = db.create_user("bob", "h", "user")
    db.upsert_request(
        type="album", foreign_id="x", title="T", artist="A", image_url=None,
        lidarr_artist_id=None, lidarr_album_id=None, status="pending", user_id=bob["id"],
    )
    assert db.list_requests(user_id=bob["id"])[0]["foreign_id"] == "x"
    assert db.list_requests(user_id=999) == []   # another user sees nothing
    assert len(db.list_requests()) == 1          # admin / all sees it
