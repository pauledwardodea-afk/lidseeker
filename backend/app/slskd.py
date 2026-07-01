"""Thin async client over slskd, used only to surface live download progress
in the request pipeline. slskd transfers are keyed by Soulseek username +
filename, not by Lidarr album id, so we match a request to its transfers
heuristically: the artist name appearing in the download path.
"""
import asyncio
import re
import time
from typing import Optional

import httpx

from . import config

_HEADERS = {"X-API-Key": config.SLSKD_API_KEY}

# One transfers fetch serves every request row when /api/requests fans out
# concurrently — cache it briefly, with a lock against cold-cache stampedes.
_TRANSFERS_TTL = 5.0
_transfers_cache: tuple[float, Optional[list]] = (0.0, None)
_transfers_lock = asyncio.Lock()


async def _downloads() -> Optional[list]:
    """Current slskd downloads, cached for _TRANSFERS_TTL. None = unreachable."""
    global _transfers_cache
    async with _transfers_lock:
        now = time.monotonic()
        if now - _transfers_cache[0] < _TRANSFERS_TTL:
            return _transfers_cache[1]
        try:
            async with _client() as c:
                r = await c.get("/transfers/downloads")
                r.raise_for_status()
                users = r.json()
        except httpx.HTTPError:
            users = None
        _transfers_cache = (time.monotonic(), users)
        return users


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{config.SLSKD_URL}/api/v0", headers=_HEADERS, timeout=10.0
    )


def _norm(s: str) -> str:
    """Lowercase alphanumerics only — for fuzzy path matching."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# slskd transfer states are strings like "Completed, Succeeded",
# "InProgress", "Queued, Remotely", "Completed, Errored", etc.
def _is_active(state: str) -> bool:
    s = (state or "").lower()
    return "completed" not in s and "cancelled" not in s


def _is_done(state: str) -> bool:
    s = (state or "").lower()
    return "completed" in s and "succeeded" in s


async def artist_progress(artist_name: Optional[str]) -> Optional[dict]:
    """Aggregate progress of any in-flight slskd downloads whose path mentions
    the artist. Returns None if slskd is unconfigured/unreachable or nothing
    matches, else {active, files_total, files_done, percent, username}.
    """
    if not config.SLSKD_API_KEY or not artist_name:
        return None
    needle = _norm(artist_name)
    if not needle:
        return None
    users = await _downloads()
    if users is None:
        return None

    size_total = bytes_done = files_total = files_done = 0
    active = False
    username = None
    for user in users:
        for directory in user.get("directories", []):
            for f in directory.get("files", []):
                path = f.get("filename", "")
                if needle not in _norm(path):
                    continue
                files_total += 1
                size = f.get("size", 0) or 0
                size_total += size
                bytes_done += f.get("bytesTransferred", 0) or 0
                state = f.get("state", "")
                if _is_done(state):
                    files_done += 1
                if _is_active(state):
                    active = True
                    username = user.get("username")

    if files_total == 0:
        return None
    percent = round(100 * bytes_done / size_total, 1) if size_total else 0.0
    return {
        "active": active,
        "files_total": files_total,
        "files_done": files_done,
        "percent": percent,
        "username": username,
    }


async def delete_searches_for(artist_name: Optional[str]) -> int:
    """Remove slskd search records whose query mentions this artist. Soularr is
    configured with delete_searches=False, so completed searches pile up in slskd;
    once a request has successfully downloaded we clean up after it. Matched the
    same fuzzy way as transfers (artist name in the search text). Returns the count
    deleted; best-effort — never raises."""
    if not config.SLSKD_API_KEY or not artist_name:
        return 0
    needle = _norm(artist_name)
    if not needle:
        return 0
    deleted = 0
    try:
        async with _client() as c:
            r = await c.get("/searches")
            r.raise_for_status()
            for s in r.json():
                if needle not in _norm(s.get("searchText", "")):
                    continue
                sid = s.get("id")
                if not sid:
                    continue
                try:
                    d = await c.delete(f"/searches/{sid}")
                    if d.status_code < 300:
                        deleted += 1
                except httpx.HTTPError:
                    continue
    except httpx.HTTPError:
        return deleted
    return deleted
