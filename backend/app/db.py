"""SQLite storage for music requests."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    type             TEXT NOT NULL,
    foreign_id       TEXT NOT NULL,
    title            TEXT,
    artist           TEXT,
    image_url        TEXT,
    lidarr_artist_id INTEGER,
    lidarr_album_id  INTEGER,
    status           TEXT NOT NULL DEFAULT 'pending',
    error            TEXT,
    created_at       TEXT NOT NULL,
    notified         INTEGER NOT NULL DEFAULT 0,
    search_attempts  INTEGER NOT NULL DEFAULT 0,
    last_attempt_at  TEXT,
    UNIQUE(type, foreign_id)
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL
);
"""


def init() -> None:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    with _conn() as c:
        c.executescript(_SCHEMA)
        # Lightweight migration for existing DBs.
        cols = {r[1] for r in c.execute("PRAGMA table_info(requests)")}
        if "notified" not in cols:
            c.execute("ALTER TABLE requests ADD COLUMN notified INTEGER NOT NULL DEFAULT 0")
        if "search_attempts" not in cols:
            c.execute("ALTER TABLE requests ADD COLUMN search_attempts INTEGER NOT NULL DEFAULT 0")
        if "last_attempt_at" not in cols:
            c.execute("ALTER TABLE requests ADD COLUMN last_attempt_at TEXT")
        if "user_id" not in cols:
            c.execute("ALTER TABLE requests ADD COLUMN user_id INTEGER")
    seed_admin_from_env()


def seed_admin_from_env() -> None:
    """Bootstrap the first admin from APP_USER/APP_PASS_HASH when there are no
    users yet, so existing single-user deployments keep working and become the
    admin. Existing requests are attributed to that admin."""
    if not config.APP_PASS_HASH:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            return
        cur = c.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (config.APP_USER, config.APP_PASS_HASH, "admin", now),
        )
        c.execute("UPDATE requests SET user_id=? WHERE user_id IS NULL", (cur.lastrowid,))


@contextmanager
def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_request(*, type: str, foreign_id: str, title: str, artist: str | None,
                   image_url: str | None, lidarr_artist_id: int | None,
                   lidarr_album_id: int | None, status: str,
                   error: str | None = None, user_id: int | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO requests (type, foreign_id, title, artist, image_url,
                                  lidarr_artist_id, lidarr_album_id, status, error,
                                  created_at, user_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(type, foreign_id) DO UPDATE SET
                title=excluded.title, artist=excluded.artist, image_url=excluded.image_url,
                lidarr_artist_id=excluded.lidarr_artist_id,
                lidarr_album_id=excluded.lidarr_album_id,
                status=excluded.status, error=excluded.error,
                -- the latest requester owns it
                user_id=COALESCE(excluded.user_id, requests.user_id),
                -- a (re)request starts the give-up clock fresh
                search_attempts=0, last_attempt_at=NULL
            """,
            (type, foreign_id, title, artist, image_url, lidarr_artist_id,
             lidarr_album_id, status, error, now, user_id),
        )
        row = c.execute(
            "SELECT * FROM requests WHERE type=? AND foreign_id=?", (type, foreign_id)
        ).fetchone()
        return dict(row)


def list_requests(user_id: int | None = None) -> list[dict]:
    """All requests, or just one user's when user_id is given."""
    with _conn() as c:
        if user_id is None:
            rows = c.execute("SELECT * FROM requests ORDER BY created_at DESC").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM requests WHERE user_id=? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def update_status(request_id: int, status: str) -> None:
    with _conn() as c:
        c.execute("UPDATE requests SET status=? WHERE id=?", (status, request_id))


def get_request(request_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        return dict(row) if row else None


def delete_request(request_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row:
            c.execute("DELETE FROM requests WHERE id=?", (request_id,))
        return dict(row) if row else None


def bump_search_attempt(request_id: int, attempts: int) -> None:
    """Record one more fruitless search cycle and stamp the time it happened."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "UPDATE requests SET search_attempts=?, last_attempt_at=? WHERE id=?",
            (attempts, now, request_id),
        )


def reset_search_attempts(request_id: int) -> None:
    """A source was found (request is progressing) — clear the give-up counter."""
    with _conn() as c:
        c.execute(
            "UPDATE requests SET search_attempts=0, last_attempt_at=NULL WHERE id=?",
            (request_id,),
        )


def mark_notified(request_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE requests SET notified=1 WHERE id=?", (request_id,))


def baseline_notifications() -> None:
    """At startup, treat all currently-available requests as already notified so
    we only push on NEW transitions, not for the existing backlog."""
    with _conn() as c:
        c.execute("UPDATE requests SET notified=1 WHERE status='available'")


def unnotified_available() -> list[dict]:
    """Resolved-as-available requests we haven't pushed a notification for yet."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM requests WHERE status='available' AND notified=0"
        ).fetchall()
        return [dict(r) for r in rows]


def active_request_foreign_ids() -> set[str]:
    """foreign_ids of requests that are live, so the UI can lock albums already
    being requested and avoid duplicates. Excludes terminal failures ('error' and
    'failed') so the user can re-request an album we gave up on."""
    with _conn() as c:
        rows = c.execute(
            "SELECT foreign_id FROM requests WHERE status NOT IN ('error', 'failed')"
        ).fetchall()
        return {r["foreign_id"] for r in rows}


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
def get_user(username: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def usernames_by_id() -> dict[int, str]:
    """Map of user id -> username, for attributing requests."""
    with _conn() as c:
        return {r["id"]: r["username"] for r in c.execute("SELECT id, username FROM users")}


def create_user(username: str, password_hash: str, role: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username, password_hash, role, now),
        )
        row = c.execute(
            "SELECT id, username, role, created_at FROM users WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def delete_user(user_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM users WHERE id=?", (user_id,))
        return cur.rowcount > 0


def count_admins() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]


def set_user_password(user_id: int, password_hash: str) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
