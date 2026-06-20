"""lidseeker — a music request backend (the 'seerr' for Lidarr)."""
import asyncio
import logging
from typing import Optional

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import auth, config, db, lidarr, notify, slskd, soularr_cfg, soularr_ctl
from .schemas import (
    ActionResult, DiscoverCategories, LoginIn, RequestIn, RequestOut, SearchResult,
    ServiceLink, Settings, SettingsIn, TokenOut, Track,
)

log = logging.getLogger("lidseeker")
app = FastAPI(title="lidseeker", version="0.1.0-beta")


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


@app.on_event("startup")
async def _startup() -> None:
    db.init()
    db.baseline_notifications()   # don't notify for already-available requests
    # Always run: besides ntfy pushes it also tracks the give-up counter and
    # cleans up slskd searches, which must happen even if ntfy is disabled.
    asyncio.create_task(_status_loop())
    asyncio.create_task(_reconcile_loop())   # keep pending requests monitored


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
async def health() -> dict:
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@app.post("/api/auth/login", response_model=TokenOut)
async def login(body: LoginIn) -> TokenOut:
    if not auth.verify_credentials(body.username, body.password):
        raise HTTPException(401, "Invalid credentials")
    return TokenOut(token=auth.issue_token(body.username))


# --------------------------------------------------------------------------
# Search & browse
# --------------------------------------------------------------------------
@app.get("/api/search", response_model=list[SearchResult])
async def search(
    term: str = Query(..., min_length=1),
    type: str = Query("album", pattern="^(artist|album|track)$"),
    _user: str = Depends(auth.require_user),
) -> list[dict]:
    if type == "track":
        return await lidarr.search_tracks(term)
    return await lidarr.search(term, type)


@app.get("/api/artist/{foreign_id}/albums", response_model=list[SearchResult])
async def artist_albums(
    foreign_id: str, _user: str = Depends(auth.require_user)
) -> list[dict]:
    return await lidarr.artist_albums(foreign_id)


@app.get("/api/album/{foreign_id}/tracks", response_model=list[Track])
async def album_tracks(
    foreign_id: str, _user: str = Depends(auth.require_user)
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
        db.update_status(req_id, "error")
        db.upsert_request(
            type=type, foreign_id=foreign_id, title="", artist=None,
            image_url=None, lidarr_artist_id=None, lidarr_album_id=None,
            status="error", error=str(e),
        )


@app.post("/api/request", response_model=RequestOut)
async def create_request(
    body: RequestIn,
    background: BackgroundTasks,
    _user: str = Depends(auth.require_user),
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
    )
    background.add_task(_process_request, row["id"], req_type, foreign_id)
    return _to_out(row)


@app.get("/api/requests", response_model=list[RequestOut])
async def list_requests(_user: str = Depends(auth.require_user)) -> list[dict]:
    out = []
    for row in db.list_requests():
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
        )
        out.append(_to_out(row, pipeline))
    return out


@app.delete("/api/requests/{rid}", response_model=ActionResult)
async def delete_request(rid: int, _user: str = Depends(auth.require_user)) -> dict:
    """Remove a request and stop it being wanted (unmonitor the album)."""
    row = db.delete_request(rid)
    if not row:
        raise HTTPException(404, "Request not found")
    if row["lidarr_album_id"]:
        try:
            await lidarr.set_album_monitored([row["lidarr_album_id"]], False)
        except httpx.HTTPError:
            pass
    return {"ok": True, "message": "Request removed."}


@app.post("/api/requests/{rid}/retry", response_model=ActionResult)
async def retry_request(rid: int, _user: str = Depends(auth.require_user)) -> dict:
    """Un-stick a failed/stuck request: clear Soularr's denylist entry, re-monitor,
    re-search, and force a Soularr run."""
    row = db.get_request(rid)
    if not row:
        raise HTTPException(404, "Request not found")
    soularr_cfg.clear_denylist_entry(row["lidarr_album_id"])
    if row["lidarr_album_id"]:
        try:
            await lidarr.set_album_monitored([row["lidarr_album_id"]], True)
            if row["lidarr_artist_id"]:
                await lidarr.set_artist_monitored(row["lidarr_artist_id"], True)
            await lidarr.album_search([row["lidarr_album_id"]])
        except httpx.HTTPError:
            pass
    # Reset the give-up counter and revive a terminal request so it searches anew.
    db.reset_search_attempts(rid)
    if row["status"] in ("error", "failed"):
        db.update_status(rid, "pending")
    # Soularr adapter only: kick its cycle now. Native clients already picked up
    # the AlbumSearch above.
    if config.SOULARR_ENABLED:
        try:
            await soularr_ctl.trigger_run()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "message": "Retrying — searching again now."}


@app.get("/api/discover/categories", response_model=DiscoverCategories)
async def discover_categories(
    genre: Optional[str] = Query(None),
    decade: Optional[int] = Query(None),
    _user: str = Depends(auth.require_user),
) -> dict:
    """Genre + decade chips for the Discover tab, each conditioned on the other
    active filter so the chips only offer combinations that have results."""
    return await lidarr.discover_categories(genre=genre, decade=decade)


@app.get("/api/discover", response_model=list[SearchResult])
async def discover(
    genre: Optional[str] = Query(None),
    decade: Optional[int] = Query(None),
    _user: str = Depends(auth.require_user),
) -> list[dict]:
    """Unowned releases from artists in your library. No filter → newest first;
    a `genre` or `decade` browses that category instead."""
    return await lidarr.discover(genre=genre, decade=decade)


@app.get("/api/settings", response_model=Settings)
async def get_settings(_user: str = Depends(auth.require_user)) -> dict:
    return {
        "quality": soularr_cfg.get_quality(),
        "ntfyTopic": config.NTFY_TOPIC or None,
        "ntfyUrl": config.NTFY_URL or None,
    }


@app.put("/api/settings", response_model=ActionResult)
async def put_settings(
    body: SettingsIn, _user: str = Depends(auth.require_user)
) -> dict:
    if not config.SOULARR_ENABLED:
        raise HTTPException(409, "Quality control is only available with the Soularr adapter.")
    try:
        changed = soularr_cfg.set_quality(body.quality)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Couldn't update quality: {e}")
    if changed:
        try:
            await soularr_ctl.trigger_run()   # restart Soularr to apply
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "message": f"Quality set to {body.quality.upper()}."}


@app.get("/api/services", response_model=list[ServiceLink])
async def services(_user: str = Depends(auth.require_user)) -> list[dict]:
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
async def search_now(_user: str = Depends(auth.require_user)) -> dict:
    """Force a search now instead of waiting for the next cycle."""
    try:
        await _search_now()
    except Exception as e:  # noqa: BLE001 — surface a clean error to the app
        log.warning("search-now failed: %s", e)
        raise HTTPException(503, "Couldn't start a search right now.")
    return {"ok": True, "message": "Searching now — this can take a minute."}


def _to_out(row: dict, pipeline: dict | None = None) -> dict:
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
    }
