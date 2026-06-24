"""lidseeker — a music request backend (the 'seerr' for Lidarr)."""
import asyncio
import logging
import mimetypes
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import bcrypt
import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, config, db, lidarr, notify, slskd, soularr_cfg, soularr_ctl
from .schemas import (
    ActionResult, DiscoverCategories, LoginIn, Me, PasswordChange, RequestIn, RequestOut,
    SearchResult, ServiceLink, Settings, SettingsIn, TokenOut, Track, User, UserCreate,
)

log = logging.getLogger("lidseeker")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup
    db.init()
    db.baseline_notifications()   # don't notify for already-available requests
    _requeue_stranded_requests()  # recover any add that a restart interrupted
    # Always run: besides ntfy pushes it also tracks the give-up counter and
    # cleans up slskd searches, which must happen even if ntfy is disabled.
    asyncio.create_task(_status_loop())
    asyncio.create_task(_reconcile_loop())   # keep pending requests monitored
    try:
        yield
    finally:
        # Shutdown
        await lidarr.aclose()   # close the shared HTTP client


app = FastAPI(title="lidseeker", version=__version__, lifespan=lifespan)


@app.exception_handler(httpx.HTTPError)
async def _upstream_error(_request: Request, exc: httpx.HTTPError) -> JSONResponse:
    """Lidarr or its metadata service hiccupped — surface a clean 502, not a 500."""
    log.warning("upstream Lidarr error: %s", exc)
    return JSONResponse(
        status_code=502,
        content={"detail": "Music service is temporarily unavailable. Please try again."},
    )

# App is a native client; allow any origin (auth is via Bearer token).
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# Baseline security headers for the served SPA. The token lives in localStorage
# (accepted tradeoff for a self-hosted JWT SPA), so a CSP is the pragmatic XSS
# mitigation. img-src allows any https host since album art comes from several
# CDNs (images.lidarr.audio, coverartarchive.org, ...); style-src needs
# 'unsafe-inline' for React inline styles.
_CSP = (
    "default-src 'self'; "
    "img-src 'self' https: data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)
# Skip the CSP on the API docs — Swagger/ReDoc load assets + inline scripts that a
# strict 'self' policy would break.
_NO_CSP_PATHS = ("/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if not request.url.path.startswith(_NO_CSP_PATHS):
        response.headers.setdefault("Content-Security-Policy", _CSP)
    return response


def _requeue_stranded_requests() -> None:
    """A request whose add task was interrupted by a restart sits 'pending' with
    nothing in Lidarr forever. Re-dispatch the add for each so it completes."""
    stranded = db.pending_unprocessed()
    for row in stranded:
        asyncio.create_task(_process_request(row["id"], row["type"], row["foreign_id"]))
    if stranded:
        log.info("requeued %d request(s) stranded by a restart", len(stranded))


async def _status_loop() -> None:
    """Background poller. For each unresolved request it: refreshes live status
    from Lidarr; counts fruitless Soularr search cycles and gives up (marks
    'failed') after too many; and, once a request becomes available, pushes an
    ntfy notification and clears out its leftover slskd searches."""
    while True:
        await asyncio.sleep(config.NOTIFY_POLL_SECONDS)
        try:
            for row in db.list_requests():
                if row["status"] in ("available", "error", "failed"):
                    continue
                if not (row["lidarr_album_id"] or row["lidarr_artist_id"]):
                    continue
                live = await lidarr.request_status(
                    row["lidarr_album_id"], row["lidarr_artist_id"]
                )
                if live != row["status"]:
                    db.update_status(row["id"], live)
                    row["status"] = live
                if live == "available":
                    continue
                await _track_search_attempts(row)
            # On-available side effects: notify once, then tidy up slskd searches.
            for row in db.unnotified_available():
                if notify.enabled():
                    await notify.publish(
                        "Ready to play 🎵",
                        f"{row['title']} — {row['artist'] or ''} is now available".strip(),
                    )
                db.mark_notified(row["id"])
                try:
                    n = await slskd.delete_searches_for(row["artist"])
                    if n:
                        log.info("cleaned up %d slskd search(es) for request %s", n, row["id"])
                except Exception:  # noqa: BLE001 — cleanup is best-effort
                    log.exception("slskd search cleanup failed for request %s", row["id"])
        except Exception:  # noqa: BLE001 — never let the loop die
            log.exception("status loop error")


async def _track_search_attempts(row: dict) -> None:
    """Count consecutive Soularr search cycles that turned up no source, and give
    up (mark the request 'failed') once we hit SEARCH_GIVE_UP_ATTEMPTS. A cycle
    only counts while the request is genuinely still searching with nothing in
    flight; any real progress resets the counter."""
    pipeline = await lidarr.request_pipeline(
        row["lidarr_album_id"], row["lidarr_artist_id"],
        row["artist"], row["status"], row["created_at"],
    )
    if pipeline["stage"] != "searching" or pipeline["failed"]:
        if row["search_attempts"]:
            db.reset_search_attempts(row["id"])
        return
    last = row["last_attempt_at"]
    if last and not _seconds_elapsed(last, config.SEARCH_ATTEMPT_INTERVAL_SECONDS):
        return
    attempts = (row["search_attempts"] or 0) + 1
    db.bump_search_attempt(row["id"], attempts)
    if attempts >= config.SEARCH_GIVE_UP_ATTEMPTS:
        db.update_status(row["id"], "failed")
        log.info("request %s gave up after %d fruitless searches", row["id"], attempts)


def _seconds_elapsed(iso_ts: str, seconds: int) -> bool:
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(iso_ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() >= seconds
    except ValueError:
        return True


async def _reconcile_loop() -> None:
    """Re-assert monitoring for not-yet-downloaded requests. Lidarr's scheduled
    'refresh all artists' periodically treats a freshly-added artist's albums as
    'new' and un-monitors them (monitorNewItems=none), which would silently
    un-want a pending request before Soularr grabs it. Re-enforce until the
    download lands; ensure_album_monitored is a cheap no-op once it's stable."""
    while True:
        await asyncio.sleep(config.RECONCILE_POLL_SECONDS)
        try:
            for row in db.list_requests():
                if row["status"] in ("available", "error", "failed"):
                    continue
                if not (row["lidarr_album_id"] and row["lidarr_artist_id"]):
                    continue
                await lidarr.ensure_album_monitored(
                    row["lidarr_artist_id"], row["lidarr_album_id"]
                )
        except Exception:  # noqa: BLE001 — never let the loop die
            log.exception("reconcile loop error")


@app.get("/api/health")
async def health() -> JSONResponse:
    """Liveness + DB reachability. Returns 503 (so the Docker healthcheck fails)
    when the database is unreachable, instead of always claiming 'ok'."""
    db_ok = db.ping()
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={
            "status": "ok" if db_ok else "degraded",
            "db": db_ok,
            "version": __version__,
        },
    )


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
# --- Login brute-force limiter (in-process; counts FAILURES only) ---
_LOGIN_MAX_FAILURES = 10
_LOGIN_WINDOW_SECONDS = 300
# {client_ip: [failure_timestamps]}
_login_failures: dict[str, list[float]] = {}
_login_attempt_counter = 0


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _login_blocked(ip: str) -> bool:
    global _login_attempt_counter
    now = time.monotonic()
    recent = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_WINDOW_SECONDS]
    if recent:
        _login_failures[ip] = recent
    else:
        _login_failures.pop(ip, None)  # prune empty entries immediately
    # Periodic full sweep so IPs that never return don't leak memory.
    _login_attempt_counter += 1
    if _login_attempt_counter % 100 == 0:
        _prune_login_failures(now)
    return len(recent) >= _LOGIN_MAX_FAILURES


def _prune_login_failures(now: float) -> None:
    """Drop entries that are entirely outside the window."""
    stale = [
        ip for ip, stamps in _login_failures.items()
        if not any(now - t < _LOGIN_WINDOW_SECONDS for t in stamps)
    ]
    for ip in stale:
        _login_failures.pop(ip, None)


def _record_login_failure(ip: str) -> None:
    _login_failures.setdefault(ip, []).append(time.monotonic())


# --------------------------------------------------------------------------
# Rate limiter — protects external APIs (MusicBrainz: ~1 req/s, Lidarr: finite)
# --------------------------------------------------------------------------
class _RateLimiter:
    """Simple sliding-window rate limiter, per-IP, per-endpoint class."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = {}
        self._hits = 0

    async def __call__(self, request: Request) -> None:
        ip = _client_ip(request)
        now = time.monotonic()
        stamps = self._buckets.get(ip, [])
        recent = [t for t in stamps if now - t < self.window_seconds]
        if len(recent) >= self.max_requests:
            raise HTTPException(429, "Too many requests. Please wait a moment.")
        recent.append(now)
        self._buckets[ip] = recent
        # Periodic full sweep so the dict doesn't grow unbounded.
        self._hits += 1
        if self._hits % 200 == 0:
            stale = [
                k for k, v in self._buckets.items()
                if not any(now - t < self.window_seconds for t in v)
            ]
            for k in stale:
                self._buckets.pop(k, None)


# MusicBrainz asks for ~1 req/s; allow bursts but cap sustained rate.
_rate_limit_search = _RateLimiter(max_requests=20, window_seconds=10)
_rate_limit_discover = _RateLimiter(max_requests=30, window_seconds=10)
_rate_limit_tracklist = _RateLimiter(max_requests=30, window_seconds=10)


@app.post("/api/auth/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request) -> TokenOut:
    ip = _client_ip(request)
    if _login_blocked(ip):
        raise HTTPException(429, "Too many failed logins. Try again in a few minutes.")
    user = auth.verify_credentials(body.username, body.password)
    if not user:
        _record_login_failure(ip)
        raise HTTPException(401, "Invalid credentials")
    _login_failures.pop(ip, None)   # clear the counter on success
    return TokenOut(token=auth.issue_token(user))


_MIN_PASSWORD_LEN = 8


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def _validate_password(password: str) -> None:
    if len(password or "") < _MIN_PASSWORD_LEN:
        raise HTTPException(
            400, f"Password must be at least {_MIN_PASSWORD_LEN} characters."
        )


@app.get("/api/me", response_model=Me)
async def me(user: auth.CurrentUser = Depends(auth.require_user)) -> dict:
    return {"username": user.username, "role": user.role}


@app.put("/api/me/password", response_model=ActionResult)
async def change_my_password(
    body: PasswordChange, user: auth.CurrentUser = Depends(auth.require_user)
) -> dict:
    if not auth.verify_credentials(user.username, body.currentPassword):
        raise HTTPException(400, "Current password is incorrect.")
    _validate_password(body.newPassword)
    db.set_user_password(user.id, _hash(body.newPassword))
    db.bump_token_version(user.id)   # sign every other session out
    return {"ok": True, "message": "Password changed."}


# --------------------------------------------------------------------------
# Users (admin only)
# --------------------------------------------------------------------------
@app.get("/api/users", response_model=list[User])
async def list_users(_admin: auth.CurrentUser = Depends(auth.require_admin)) -> list[dict]:
    return [
        {"id": u["id"], "username": u["username"], "role": u["role"], "createdAt": u["created_at"]}
        for u in db.list_users()
    ]


@app.post("/api/users", response_model=User)
async def create_user(
    body: UserCreate, _admin: auth.CurrentUser = Depends(auth.require_admin)
) -> dict:
    username = body.username.strip()
    if not username:
        raise HTTPException(400, "Username is required.")
    _validate_password(body.password)
    if db.get_user(username):
        raise HTTPException(409, "That username is already taken.")
    u = db.create_user(username, _hash(body.password), body.role)
    return {"id": u["id"], "username": u["username"], "role": u["role"], "createdAt": u["created_at"]}


@app.delete("/api/users/{uid}", response_model=ActionResult)
async def delete_user(
    uid: int, admin: auth.CurrentUser = Depends(auth.require_admin)
) -> dict:
    target = db.get_user_by_id(uid)
    if not target:
        raise HTTPException(404, "User not found.")
    if uid == admin.id:
        raise HTTPException(400, "You can't delete your own account.")
    if target["role"] == "admin" and db.count_admins() <= 1:
        raise HTTPException(400, "Can't delete the last admin.")
    db.delete_user(uid)
    return {"ok": True, "message": f"Removed {target['username']}."}


# --------------------------------------------------------------------------
# Search & browse
# --------------------------------------------------------------------------
@app.get("/api/search", response_model=list[SearchResult])
async def search(
    term: str = Query(..., min_length=1),
    type: str = Query("album", pattern="^(artist|album|track)$"),
    _user: auth.CurrentUser = Depends(auth.require_user),
    _rl: None = Depends(_rate_limit_search),
) -> list[dict]:
    if type == "track":
        return await lidarr.search_tracks(term)
    return await lidarr.search(term, type)


@app.get("/api/artist/{foreign_id}/albums", response_model=list[SearchResult])
async def artist_albums(
    foreign_id: str, _user: auth.CurrentUser = Depends(auth.require_user),
    _rl: None = Depends(_rate_limit_search),
) -> list[dict]:
    return await lidarr.artist_albums(foreign_id)


@app.get("/api/album/{foreign_id}/tracks", response_model=list[Track])
async def album_tracks(
    foreign_id: str, _user: auth.CurrentUser = Depends(auth.require_user),
    _rl: None = Depends(_rate_limit_tracklist),
) -> list[dict]:
    """Tracklist for an album (for the expandable song view)."""
    return await lidarr.album_tracks(foreign_id)


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------
async def _process_request(req_id: int, type: str, foreign_id: str) -> None:
    try:
        if type == "album":
            res = await lidarr.request_album(foreign_id)
            # If Lidarr never materialised the album under its artist (common for
            # Various Artists / compilation releases), there's nothing to monitor
            # or search — fail loudly so the request shows an error + Retry rather
            # than sitting "pending" forever.
            if not res.get("lidarr_album_id"):
                raise RuntimeError(
                    "Couldn't find this album in Lidarr to download — it may be a "
                    "compilation or have no matching release."
                )
        else:
            res = await lidarr.request_artist(foreign_id)
        status = await lidarr.request_status(
            res["lidarr_album_id"], res["lidarr_artist_id"]
        )
        db.upsert_request(
            type=type, foreign_id=foreign_id, title=res["title"],
            artist=res["artist"], image_url=res["imageUrl"],
            lidarr_artist_id=res["lidarr_artist_id"],
            lidarr_album_id=res["lidarr_album_id"], status=status,
        )
    except Exception as e:  # noqa: BLE001 — surface failure on the request row
        log.exception("request %s failed", req_id)
        # Keep the title/art shown on the row — just flag the error + offer Retry.
        db.set_request_error(req_id, str(e))


@app.post("/api/request", response_model=RequestOut)
async def create_request(
    body: RequestIn,
    background: BackgroundTasks,
    user: auth.CurrentUser = Depends(auth.require_user),
) -> dict:
    req_type, foreign_id = body.type, body.foreignId

    # A song request currently fulfils by adding its parent album through the
    # proven album pipeline, so collapse it to an album request up front. (The
    # single-track download path will branch on body.mode == "track" here.)
    if req_type == "track":
        if not body.albumForeignId:
            raise HTTPException(400, "Track request requires albumForeignId")
        req_type, foreign_id = "album", body.albumForeignId

    # Fast metadata lookup so the request shows title/art immediately.
    if req_type == "album":
        info = await lidarr.lookup_album_by_id(foreign_id)
        if not info:
            raise HTTPException(404, "Album not found")
        norm = lidarr.normalize_album(info)
    else:
        info = await lidarr.lookup_artist_by_id(foreign_id)
        if not info:
            raise HTTPException(404, "Artist not found")
        norm = lidarr.normalize_artist(info)

    row = db.upsert_request(
        type=req_type, foreign_id=foreign_id, title=norm["title"],
        artist=norm["artist"], image_url=norm["imageUrl"],
        lidarr_artist_id=None, lidarr_album_id=None, status="pending",
        user_id=user.id,
    )
    background.add_task(_process_request, row["id"], req_type, foreign_id)
    return _to_out(row)


# Resolve at most this many requests' live status concurrently. Bounds the burst
# of Lidarr/slskd calls when someone has a long request list.
_REQUESTS_CONCURRENCY = 8


@app.get("/api/requests", response_model=list[RequestOut])
async def list_requests(user: auth.CurrentUser = Depends(auth.require_user)) -> list[dict]:
    # Admins see everyone's requests (with the requester shown); others see only theirs.
    rows = db.list_requests() if user.is_admin else db.list_requests(user_id=user.id)
    names = db.usernames_by_id() if user.is_admin else {}
    # Fetch Lidarr's download queue ONCE for the whole batch (not per row).
    queue_index = await lidarr.queue_index()
    sem = asyncio.Semaphore(_REQUESTS_CONCURRENCY)

    async def _resolve(row: dict) -> dict:
        async with sem:
            # Refresh live status from Lidarr for resolved requests. 'error' and
            # 'failed' are terminal — don't let a 0% album flip them back to pending.
            if row["status"] not in ("error", "failed") and (
                row["lidarr_album_id"] or row["lidarr_artist_id"]
            ):
                live = await lidarr.request_status(
                    row["lidarr_album_id"], row["lidarr_artist_id"]
                )
                if live != row["status"]:
                    db.update_status(row["id"], live)
                    row["status"] = live
            # Detailed pipeline for the expandable My Requests view.
            pipeline = await lidarr.request_pipeline(
                row["lidarr_album_id"], row["lidarr_artist_id"],
                row["artist"], row["status"], row["created_at"],
                queue_index=queue_index,
            )
            return _to_out(row, pipeline, requested_by=names.get(row.get("user_id")))

    # gather preserves input order, so rows stay newest-first.
    return await asyncio.gather(*(_resolve(row) for row in rows))


@app.delete("/api/requests/{rid}", response_model=ActionResult)
async def delete_request(rid: int, user: auth.CurrentUser = Depends(auth.require_user)) -> dict:
    """Remove a request and stop it being wanted (unmonitor the album)."""
    existing = db.get_request(rid)
    if not existing:
        raise HTTPException(404, "Request not found")
    if not user.is_admin and existing.get("user_id") != user.id:
        raise HTTPException(403, "That isn't your request.")
    row = db.delete_request(rid)
    warn = ""
    if row["lidarr_album_id"]:
        try:
            await lidarr.set_album_monitored([row["lidarr_album_id"]], False)
        except httpx.HTTPError as e:
            log.warning("delete_request %s: couldn't unmonitor album in Lidarr: %s", rid, e)
            warn = " (couldn't reach Lidarr to unmonitor it — it may still be wanted)"
    return {"ok": True, "message": "Request removed." + warn}


@app.post("/api/requests/{rid}/retry", response_model=ActionResult)
async def retry_request(
    rid: int, background: BackgroundTasks,
    user: auth.CurrentUser = Depends(auth.require_user),
) -> dict:
    """Un-stick a failed/stuck request: clear Soularr's denylist entry, re-monitor,
    re-search, and force a Soularr run. If the request never made it into Lidarr,
    re-run the whole add pipeline."""
    row = db.get_request(rid)
    if not row:
        raise HTTPException(404, "Request not found")
    if not user.is_admin and row.get("user_id") != user.id:
        raise HTTPException(403, "That isn't your request.")
    soularr_cfg.clear_denylist_entry(row["lidarr_album_id"])
    db.reset_search_attempts(rid)

    # Never got added to Lidarr (errored before the album resolved) — re-add it.
    if not row["lidarr_album_id"]:
        db.update_status(rid, "pending")
        background.add_task(_process_request, rid, row["type"], row["foreign_id"])
        return {"ok": True, "message": "Retrying — re-adding to Lidarr."}

    # Re-monitor + re-search. Surface a real error instead of a false "Retrying!".
    try:
        await lidarr.set_album_monitored([row["lidarr_album_id"]], True)
        if row["lidarr_artist_id"]:
            await lidarr.set_artist_monitored(row["lidarr_artist_id"], True)
        await lidarr.album_search([row["lidarr_album_id"]])
    except httpx.HTTPError as e:
        log.warning("retry_request %s: Lidarr re-monitor/search failed: %s", rid, e)
        raise HTTPException(503, "Couldn't reach Lidarr to retry — try again shortly.") from e
    if row["status"] in ("error", "failed"):
        db.update_status(rid, "pending")
    # Soularr adapter only: kick its cycle now. The AlbumSearch above already
    # nudged native clients, so a failed restart here is non-fatal — just log it.
    if config.SOULARR_ENABLED:
        try:
            await soularr_ctl.trigger_run()
        except Exception as e:  # noqa: BLE001
            log.warning("retry_request %s: Soularr trigger failed: %s", rid, e)
    return {"ok": True, "message": "Retrying — searching again now."}


@app.get("/api/discover/categories", response_model=DiscoverCategories)
async def discover_categories(
    genre: Optional[str] = Query(None),
    decade: Optional[int] = Query(None),
    _user: auth.CurrentUser = Depends(auth.require_user),
    _rl: None = Depends(_rate_limit_discover),
) -> dict:
    """Genre + decade chips for the Discover tab, each conditioned on the other
    active filter so the chips only offer combinations that have results."""
    return await lidarr.discover_categories(genre=genre, decade=decade)


@app.get("/api/discover", response_model=list[SearchResult])
async def discover(
    genre: Optional[str] = Query(None),
    decade: Optional[int] = Query(None),
    _user: auth.CurrentUser = Depends(auth.require_user),
    _rl: None = Depends(_rate_limit_discover),
) -> list[dict]:
    """Unowned releases from artists in your library. No filter → newest first;
    a `genre` or `decade` browses that category instead."""
    return await lidarr.discover(genre=genre, decade=decade)


@app.get("/api/settings", response_model=Settings)
async def get_settings(user: auth.CurrentUser = Depends(auth.require_user)) -> dict:
    # The ntfy topic is a shared secret (anyone with it reads the notifications),
    # so only admins see it — non-admins get nulls.
    return {
        "quality": soularr_cfg.get_quality(),
        "ntfyTopic": (config.NTFY_TOPIC or None) if user.is_admin else None,
        "ntfyUrl": (config.NTFY_URL or None) if user.is_admin else None,
    }


@app.put("/api/settings", response_model=ActionResult)
async def put_settings(
    body: SettingsIn, _user: auth.CurrentUser = Depends(auth.require_user)
) -> dict:
    if not config.SOULARR_ENABLED:
        raise HTTPException(409, "Quality control is only available with the Soularr adapter.")
    try:
        changed = soularr_cfg.set_quality(body.quality)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Couldn't update quality: {e}") from e
    if changed:
        try:
            await soularr_ctl.trigger_run()   # restart Soularr to apply
        except Exception as e:  # noqa: BLE001
            log.warning("put_settings: Soularr restart failed: %s", e)
            return {
                "ok": True,
                "message": f"Quality set to {body.quality.upper()}, but Soularr "
                           "didn't restart — it'll apply on the next cycle.",
            }
    return {"ok": True, "message": f"Quality set to {body.quality.upper()}."}


@app.get("/api/services", response_model=list[ServiceLink])
async def services(_user: auth.CurrentUser = Depends(auth.require_user)) -> list[dict]:
    """Links to the pipeline services (from SERVICE_LINKS), shown under a request."""
    return config.SERVICE_LINKS


async def _search_now() -> None:
    """Kick off a search for every unresolved request, right now.

    Soularr adapter: restart the Soularr container so it re-runs its cycle.
    Native: ask Lidarr to search its indexers for the wanted albums (the
    download client takes it from there)."""
    if config.SOULARR_ENABLED:
        await soularr_ctl.trigger_run()
        return
    album_ids = [
        row["lidarr_album_id"]
        for row in db.list_requests()
        if row["lidarr_album_id"]
        and row["status"] not in ("available", "error", "failed")
    ]
    if album_ids:
        await lidarr.album_search(album_ids)


@app.post("/api/search-now", response_model=ActionResult)
@app.post("/api/soularr/run", response_model=ActionResult)  # legacy alias
async def search_now(_user: auth.CurrentUser = Depends(auth.require_user)) -> dict:
    """Force a search now instead of waiting for the next cycle."""
    try:
        await _search_now()
    except Exception as e:  # noqa: BLE001 — surface a clean error to the app
        log.warning("search-now failed: %s", e)
        raise HTTPException(503, "Couldn't start a search right now.") from e
    return {"ok": True, "message": "Searching now — this can take a minute."}


def _to_out(row: dict, pipeline: dict | None = None, requested_by: str | None = None) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "foreignId": row["foreign_id"],
        "title": row["title"],
        "artist": row["artist"],
        "imageUrl": row["image_url"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "pipeline": pipeline,
        "requestedBy": requested_by,
    }


# --------------------------------------------------------------------------
# Web UI (the built SPA). Registered LAST so every /api route + /docs win.
# --------------------------------------------------------------------------
_WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_WEB_DIR):
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    _assets = os.path.join(_WEB_DIR, "assets")
    if os.path.isdir(_assets):
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str) -> FileResponse:
        # Unknown /api paths stay JSON 404s, not the HTML shell.
        if full_path.startswith("api/") or full_path in ("api", "docs", "openapi.json", "redoc"):
            raise HTTPException(404, "Not found")
        # Serve a real file (favicon, etc.) if present, else the SPA shell so
        # client-side routes like /requests load correctly on a hard refresh.
        candidate = os.path.normpath(os.path.join(_WEB_DIR, full_path))
        if full_path and candidate.startswith(os.path.abspath(_WEB_DIR)) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_WEB_DIR, "index.html"))
