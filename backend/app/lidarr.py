"""Async client wrapping the Lidarr v1 endpoints lidseeker uses.

Lidarr is artist-centric: to "request an album" we ensure its artist exists in
the library (added with no albums monitored), then monitor just the requested
album. Soularr's own cycle then searches slskd for monitored+missing albums.
"""
import asyncio
import contextlib
import time
from collections import Counter
from datetime import date
from typing import Optional

import httpx

from . import config, db

_API = f"{config.LIDARR_URL}/api/v1"
_HEADERS = {"X-Api-Key": config.LIDARR_API_KEY}

# Cover-type preference per result kind.
_IMAGE_PREF = {
    "album": ("cover", "disc"),
    "artist": ("poster", "fanart", "cover", "banner"),
}

# One shared, connection-pooled client for the whole app instead of a fresh
# TCP+TLS handshake per call. Created lazily inside the running loop and reused;
# `_client()` stays an `async with` context manager but no longer closes the
# client on exit, so existing call-sites are unchanged.
_shared_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(base_url=_API, headers=_HEADERS, timeout=30.0)
    return _shared_client


@contextlib.asynccontextmanager
async def _client():
    yield _get_client()


async def aclose() -> None:
    """Close the shared client on app shutdown."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


# --------------------------------------------------------------------------
# Image normalization
# --------------------------------------------------------------------------
def _best_image(images: list, kind: str) -> Optional[str]:
    """Return an absolute image URL the app can load directly.

    Lidarr serves album art via absolute images.lidarr.audio URLs (usable).
    Artist art only comes back as local /config or Forms-auth'd /MediaCover
    paths that the API key can't fetch, so artists fall back to None and the
    app shows a placeholder.
    """
    if not images:
        return None
    by_type = {img.get("coverType"): img for img in images}
    chosen = None
    for pref in _IMAGE_PREF.get(kind, ()):
        if pref in by_type:
            chosen = by_type[pref]
            break
    if chosen is None:
        chosen = images[0]

    remote = chosen.get("remoteUrl") or ""
    return remote if remote.startswith("http") else None


def _year(date_str: Optional[str]) -> Optional[int]:
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None


def normalize_artist(item: dict) -> dict:
    return {
        "type": "artist",
        "foreignId": item.get("foreignArtistId"),
        "title": item.get("artistName"),
        "artist": None,
        "year": None,
        "albumType": None,
        "imageUrl": _best_image(item.get("images", []), "artist"),
        "inLibrary": bool(item.get("id")),
    }


def normalize_album(item: dict) -> dict:
    artist = item.get("artist", {}) or {}
    # "In library" means we actually HAVE the album (its tracks are downloaded),
    # not merely that Lidarr knows the album row. Once an artist is in the
    # library every album carries an id + statistics, so a bare id is true for
    # the whole discography — use the downloaded file count instead. Fall back
    # to id only for metadata/search results that carry no statistics.
    stats = item.get("statistics")
    if stats is not None:
        in_library = (stats.get("trackFileCount") or 0) > 0
    else:
        in_library = bool(item.get("id"))
    return {
        "type": "album",
        "foreignId": item.get("foreignAlbumId"),
        "title": item.get("title"),
        "artist": artist.get("artistName"),
        "year": _year(item.get("releaseDate")),
        "albumType": item.get("albumType"),
        "imageUrl": _best_image(item.get("images", []), "album"),
        "inLibrary": in_library,
        "requested": False,   # set by artist_albums via the requests DB
    }


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
async def search(term: str, kind: str) -> list[dict]:
    path = "/artist/lookup" if kind == "artist" else "/album/lookup"
    norm = normalize_artist if kind == "artist" else normalize_album
    async with _client() as c:
        r = await c.get(path, params={"term": term})
        r.raise_for_status()
        return [norm(x) for x in r.json()]


# --------------------------------------------------------------------------
# Track search (MusicBrainz)
# --------------------------------------------------------------------------
# Lidarr has no track lookup, but a song's parent album is all we need: the
# MusicBrainz recording search returns each track's release-groups, and a
# release-group MBID IS Lidarr's foreignAlbumId. So a track result carries its
# album's foreignId and a normal album request takes it from there.
_MB_URL = "https://musicbrainz.org/ws/2"
# MusicBrainz requires a descriptive User-Agent with contact info.
_MB_HEADERS = {"User-Agent": config.MUSICBRAINZ_USER_AGENT}
# Prefer a real studio album over compilations/live/soundtrack pressings.
_RG_TYPE_RANK = {"Album": 0, "EP": 1, "Single": 2}


def _norm_text(s: Optional[str]) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _pick_release_group(rec: dict) -> Optional[dict]:
    """Choose the best parent release-group for a recording: prefer an official
    primary Album/EP with no compilation/live secondary types, earliest first."""
    best = None
    best_key = None
    for rel in rec.get("releases", []):
        rg = rel.get("release-group") or {}
        rg_id = rg.get("id")
        if not rg_id:
            continue
        primary = rg.get("primary-type") or ""
        secondary = rg.get("secondary-types") or []
        rank = _RG_TYPE_RANK.get(primary, 9)
        # Penalise compilations/live/soundtracks so the canonical album wins.
        penalty = 0 if not secondary else 5
        rg_date = rel.get("date") or rg.get("first-release-date") or "9999"
        key = (rank + penalty, rg_date)
        if best_key is None or key < best_key:
            best_key, best = key, {
                "id": rg_id,
                "title": rg.get("title") or rel.get("title"),
                "primary": primary,
                "date": rg_date,
            }
    return best


async def search_tracks(term: str, limit: int = 25) -> list[dict]:
    """Search individual songs via MusicBrainz; each result points at the album
    it belongs to so the existing album request flow can fulfil it."""
    params = {"query": term, "fmt": "json", "limit": limit}
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=_MB_HEADERS) as c:
            r = await c.get(f"{_MB_URL}/recording", params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return []

    requested = db.active_request_foreign_ids()
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rec in data.get("recordings", []):
        rg = _pick_release_group(rec)
        if not rg:
            continue
        credit = rec.get("artist-credit") or []
        artist_name = credit[0].get("name") if credit else None
        title = rec.get("title")
        # Collapse the many release pressings of the same song into one row.
        dedupe = (_norm_text(title), _norm_text(artist_name))
        if dedupe in seen:
            continue
        seen.add(dedupe)
        out.append({
            "type": "track",
            "foreignId": rec.get("id"),
            "title": title,
            "artist": artist_name,
            "year": _year(rg.get("date")),
            "albumType": rg.get("primary"),
            "imageUrl": _coverart_url(rg["id"]),
            "inLibrary": False,
            "requested": rg["id"] in requested,
            "albumForeignId": rg["id"],
            "albumTitle": rg.get("title"),
        })
    return out


# A release-group's tracklist never changes, so cache it for the process life.
# Bounded so a long-running server browsing many albums can't grow it forever.
_TRACKLIST_CACHE_MAX = 512
_tracklist_cache: dict[str, list[dict]] = {}


async def album_tracks(foreign_album_id: str) -> list[dict]:
    """Tracklist for an album, by its release-group MBID (works whether or not
    the album is in the Lidarr library, since it comes from MusicBrainz).

    A release-group has many release pressings; pick one canonical official
    release and return its tracks (ordered, multi-disc aware)."""
    if foreign_album_id in _tracklist_cache:
        return _tracklist_cache[foreign_album_id]

    try:
        async with httpx.AsyncClient(timeout=20.0, headers=_MB_HEADERS) as c:
            rg = await c.get(
                f"{_MB_URL}/release-group/{foreign_album_id}",
                params={"inc": "releases", "fmt": "json"},
            )
            rg.raise_for_status()
            releases = rg.json().get("releases", [])
            release_id = _pick_release(releases)
            if not release_id:
                return []
            rel = await c.get(
                f"{_MB_URL}/release/{release_id}",
                params={"inc": "recordings", "fmt": "json"},
            )
            rel.raise_for_status()
            media = rel.json().get("media", [])
    except (httpx.HTTPError, ValueError):
        return []

    out: list[dict] = []
    for medium in media:
        medium_no = medium.get("position") or 1
        for t in medium.get("tracks", []):
            title = t.get("title") or (t.get("recording") or {}).get("title")
            if not title:
                continue
            length = t.get("length") or (t.get("recording") or {}).get("length")
            out.append({
                "position": t.get("position") or t.get("number") or len(out) + 1,
                "title": title,
                "durationMs": int(length) if length else None,
                "mediumNumber": medium_no,
            })
    if len(_tracklist_cache) >= _TRACKLIST_CACHE_MAX:
        del _tracklist_cache[next(iter(_tracklist_cache))]
    _tracklist_cache[foreign_album_id] = out
    return out


def _pick_release(releases: list[dict]) -> Optional[str]:
    """Choose one release pressing to read the tracklist from: prefer Official,
    then the earliest dated, falling back to the first release available."""
    if not releases:
        return None
    def key(rel: dict):
        official = 0 if (rel.get("status") == "Official") else 1
        return (official, rel.get("date") or "9999")
    return min(releases, key=key).get("id")


async def lookup_album_by_id(foreign_album_id: str) -> Optional[dict]:
    async with _client() as c:
        r = await c.get("/album/lookup", params={"term": f"lidarr:{foreign_album_id}"})
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None


async def lookup_artist_by_id(foreign_artist_id: str) -> Optional[dict]:
    async with _client() as c:
        r = await c.get("/artist/lookup", params={"term": f"lidarr:{foreign_artist_id}"})
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None


async def artist_albums(foreign_artist_id: str) -> list[dict]:
    """Albums for an artist (library if present, else metadata server)."""
    existing = await find_library_artist(foreign_artist_id)
    if existing:
        async with _client() as c:
            r = await c.get("/album", params={"artistId": existing["id"]})
            r.raise_for_status()
            albums = [normalize_album(x) for x in r.json()]
    else:
        albums = await _metadata_artist_albums(foreign_artist_id)

    # Lock albums that already have a live request so they can't be re-requested.
    requested = db.active_request_foreign_ids()
    for a in albums:
        if a["foreignId"] in requested:
            a["requested"] = True
    return albums


# Lidarr's own /album/lookup can't list a non-library artist's discography
# (`lidarrid:` returns []). Lidarr itself populates albums after an add via its
# metadata server (SkyHook) — query that directly so browsing works pre-add.
_METADATA_URL = "https://api.lidarr.audio/api/v0.4"
_BROWSE_ALBUM_TYPES = {"Album", "EP"}
# Artist-screen browsing also surfaces singles (shown under a "Singles & EPs" tab).
_ARTIST_BROWSE_TYPES = {"Album", "EP", "Single"}


def _coverart_url(release_group_mbid: Optional[str]) -> Optional[str]:
    if not release_group_mbid:
        return None
    return f"https://coverartarchive.org/release-group/{release_group_mbid}/front-500"


async def _metadata_artist_albums(foreign_artist_id: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{_METADATA_URL}/artist/{foreign_artist_id}")
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return []

    name = data.get("artistname") or data.get("artistName")
    out: list[dict] = []
    for a in data.get("Albums") or data.get("albums") or []:
        atype = a.get("Type") or a.get("type")
        statuses = a.get("ReleaseStatuses") or a.get("releaseStatuses") or []
        if atype not in _ARTIST_BROWSE_TYPES or "Official" not in statuses:
            continue
        out.append({
            "type": "album",
            "foreignId": a.get("Id") or a.get("id"),
            "title": a.get("Title") or a.get("title"),
            "artist": name,
            "year": _year(a.get("ReleaseDate") or a.get("releaseDate")),
            "albumType": atype,
            # Per-album art isn't in the artist payload, but the foreignId is the
            # release-group MBID, so Cover Art Archive serves the cover directly.
            # Coil loads it; a 404 falls back to the placeholder.
            "imageUrl": _coverart_url(a.get("Id") or a.get("id")),
            "inLibrary": False,
            "requested": False,
        })
    out.sort(key=lambda x: x["year"] or 0, reverse=True)
    return out


# --------------------------------------------------------------------------
# Discover
# --------------------------------------------------------------------------
# The discover pool (every unowned Album/EP your library artists have) is the
# same /album fetch (~3.7k rows) for both the feed and its category facets, and
# it barely changes minute-to-minute — cache it briefly so flipping between
# category chips doesn't re-hammer Lidarr each tap.
_POOL_TTL = 60.0
_pool_cache: tuple[float, list[tuple[str, list[str], dict]]] = (0.0, [])


async def _discover_pool() -> list[tuple[str, list[str], dict]]:
    """(releaseDate, genres, raw album) for every released, unowned Album/EP."""
    global _pool_cache
    now = time.monotonic()
    if now - _pool_cache[0] < _POOL_TTL and _pool_cache[1]:
        return _pool_cache[1]
    async with _client() as c:
        r = await c.get("/album")
        r.raise_for_status()
        albums = r.json()
    today = date.today().isoformat()
    pool: list[tuple[str, list[str], dict]] = []
    for a in albums:
        st = a.get("statistics") or {}
        if (st.get("trackFileCount") or 0) > 0:            # already own it
            continue
        if a.get("albumType") not in _BROWSE_ALBUM_TYPES:  # albums/EPs only
            continue
        rel = a.get("releaseDate")
        if not rel or rel[:10] > today:                    # skip undated/future
            continue
        pool.append((rel, a.get("genres") or [], a))
    _pool_cache = (now, pool)
    return pool


def _decade_of(release_date: str) -> Optional[int]:
    y = _year(release_date)
    if not y or y < 1900:   # guard bogus 0000 dates from sloppy metadata
        return None
    return (y // 10) * 10


async def discover(
    limit: int = 40, genre: Optional[str] = None, decade: Optional[int] = None,
) -> list[dict]:
    """Released-but-unowned albums by artists already in your library.

    No filter → newest first ("new from artists you follow"). With a `genre` or
    `decade` it browses that category instead, still newest-first within it.
    """
    pool = await _discover_pool()
    requested = db.active_request_foreign_ids()
    scored: list[tuple[str, dict]] = []
    for rel, genres, a in pool:
        if genre and genre not in genres:
            continue
        if decade is not None and _decade_of(rel) != decade:
            continue
        norm = normalize_album(a)
        if norm["foreignId"] in requested:
            norm["requested"] = True
        scored.append((rel, norm))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in scored[:limit]]


async def discover_categories(
    genre: Optional[str] = None, decade: Optional[int] = None, top_genres: int = 12,
) -> dict:
    """Facets for the Discover chips, each conditioned on the OTHER active axis so
    the chips reflect what actually combines: genre chips are the top genres
    *within the selected decade*, and decade chips are the decades that *contain
    the selected genre*. The active selection itself is always kept in its list
    even if it falls outside the top-N, so a selected chip never disappears."""
    pool = await _discover_pool()
    genre_counts: Counter = Counter()
    decades: set[int] = set()
    for rel, genres, _ in pool:
        d = _decade_of(rel)
        if decade is None or d == decade:               # genres in the chosen decade
            genre_counts.update(genres)
        if (genre is None or genre in genres) and d is not None:  # decades with the chosen genre
            decades.add(d)

    top = [g for g, _ in genre_counts.most_common(top_genres)]
    if genre and genre not in top:
        top.append(genre)
    decade_list = sorted(decades, reverse=True)
    if decade is not None and decade not in decade_list:
        decade_list.append(decade)
        decade_list.sort(reverse=True)
    return {"genres": top, "decades": decade_list}


# --------------------------------------------------------------------------
# Library state + mutations
# --------------------------------------------------------------------------
async def find_library_artist(foreign_artist_id: str) -> Optional[dict]:
    async with _client() as c:
        r = await c.get("/artist")
        r.raise_for_status()
        for a in r.json():
            if a.get("foreignArtistId") == foreign_artist_id:
                return a
    return None


async def add_artist(lookup_artist: dict, monitor: str, search_missing: bool) -> dict:
    """POST a fresh artist into the library.

    `monitorNewItems` MUST track the request scope. Left unset it defaults to
    "all", and the RefreshArtist that follows an album request then re-monitors
    the entire discography — so a single-album request silently grabs every
    album. For an album request (monitor="none") we pin it to "none"; only an
    artist request (monitor="all") wants the whole discography.
    """
    payload = dict(lookup_artist)
    payload.update(
        {
            "qualityProfileId": config.QUALITY_PROFILE_ID,
            "metadataProfileId": config.METADATA_PROFILE_ID,
            "rootFolderPath": config.ROOT_FOLDER_PATH,
            "monitored": True,
            "monitorNewItems": "all" if monitor == "all" else "none",
            "addOptions": {"monitor": monitor, "searchForMissingAlbums": search_missing},
        }
    )
    async with _client() as c:
        r = await c.post("/artist", json=payload)
        r.raise_for_status()
        return r.json()


async def _albums_for_artist_id(artist_id: int) -> list[dict]:
    """Return EVERY album for an artist, paginating through Lidarr's API.
    The default page size is small (~15) — without pagination we'd silently
    miss albums for artists with large discographies."""
    all_albums: list[dict] = []
    page = 1
    page_size = 100
    while True:
        async with _client() as c:
            r = await c.get("/album", params={
                "artistId": artist_id, "page": page, "pageSize": page_size,
            })
            r.raise_for_status()
            page_data = r.json()
        if not page_data:
            break
        all_albums.extend(page_data)
        if len(page_data) < page_size:
            break
        page += 1
    return all_albums


async def _wait_for_album(artist_id: int, foreign_album_id: str,
                          tries: int = 20, delay: float = 1.5) -> Optional[dict]:
    """After adding a new artist Lidarr imports its albums asynchronously."""
    for _ in range(tries):
        for alb in await _albums_for_artist_id(artist_id):
            if alb.get("foreignAlbumId") == foreign_album_id:
                return alb
        await asyncio.sleep(delay)
    return None


async def set_album_monitored(album_ids: list[int], monitored: bool = True) -> None:
    async with _client() as c:
        r = await c.put("/album/monitor", json={"albumIds": album_ids, "monitored": monitored})
        r.raise_for_status()


async def set_artist_monitored(artist_id: int, monitored: bool = True) -> None:
    """Flip the artist's top-level `monitored` flag WITHOUT changing which of its
    albums are monitored. Lidarr only includes albums of *monitored artists* in
    its wanted/missing list (the source Soularr reads), so a requested album on an
    otherwise-unmonitored artist never gets grabbed. This is a plain artist PUT
    with no addOptions, so per-album monitoring is left untouched — only the
    already-monitored album(s) stay wanted, never the whole discography."""
    async with _client() as c:
        r = await c.get(f"/artist/{artist_id}")
        r.raise_for_status()
        artist = r.json()
        if artist.get("monitored") == monitored:
            return
        artist["monitored"] = monitored
        r = await c.put(f"/artist/{artist_id}", json=artist)
        r.raise_for_status()


async def album_search(album_ids: list[int]) -> None:
    async with _client() as c:
        await c.post("/command", json={"name": "AlbumSearch", "albumIds": album_ids})


async def _run_command(name: str, wait: bool = True, timeout: float = 60.0,
                       **fields) -> None:
    """Fire a Lidarr command and (by default) block until it finishes.

    Commands like RefreshArtist run asynchronously; if we don't wait, a refresh
    can complete *after* we've monitored an album and silently revert it. Polling
    the command to completion makes the sequence deterministic."""
    async with _client() as c:
        r = await c.post("/command", json={"name": name, **fields})
        r.raise_for_status()
        cmd_id = r.json().get("id")
        if not wait or not cmd_id:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            rr = await c.get(f"/command/{cmd_id}")
            rr.raise_for_status()
            if rr.json().get("status") in ("completed", "failed", "aborted"):
                return


async def refresh_artist(artist_id: int) -> None:
    """Refresh the artist and wait for it to finish (see _run_command)."""
    await _run_command("RefreshArtist", artistId=artist_id)


async def _get_album(album_id: int) -> dict:
    async with _client() as c:
        r = await c.get(f"/album/{album_id}")
        r.raise_for_status()
        return r.json()


async def _monitor_album(artist_id: int, album_id: int) -> bool:
    """Monitor exactly one album and build its track list WITHOUT touching any
    other albums on the artist. Safe for artists with a huge, carefully curated
    discography — only the target album is ever monitored or refreshed.

    The trap: Lidarr only builds an album's track list while that album is
    monitored, but with monitorNewItems="none" a RefreshArtist un-monitors a
    "new" album. So we:
      1. Monitor just the target album.
      2. Blocking RefreshArtist → builds its track list (may un-monitor it).
      3. Re-monitor — now it's no longer "new", so it sticks.
    """
    # 1) Monitor just the one album we want — nothing else.
    await set_album_monitored([album_id], True)

    # 2) Refresh to build its track list (blocking — wait for completion).
    await refresh_artist(artist_id)

    # 3) Re-assert: RefreshArtist with monitorNewItems="none" un-monitors
    #    albums that are still "new". Now that we've refreshed, the album is
    #    no longer "new" and this second monitor call sticks permanently.
    await set_album_monitored([album_id], True)

    # Verify: the album should now be monitored with a populated track list.
    album = await _get_album(album_id)
    tracks = (album.get("statistics") or {}).get("trackCount") or 0
    return bool(album.get("monitored") and tracks > 0)


async def ensure_album_monitored(artist_id: int, album_id: int) -> bool:
    """Re-assert monitoring for one requested album if Lidarr's scheduled
    'refresh all' has reverted it. Cheap no-op when it's already correctly set
    up (monitored, track list present, artist on) or already downloading — only
    re-runs the full monitor sequence when a revert is detected. Called on a
    loop until the request is satisfied."""
    try:
        album = await _get_album(album_id)
    except httpx.HTTPError:
        return False
    stats = album.get("statistics") or {}
    tracks = stats.get("trackCount") or 0
    files = stats.get("trackFileCount") or 0
    # Already downloading/done, or correctly wanted with its tracks → leave it.
    if files > 0:
        return True
    if album.get("monitored") and tracks > 0:
        try:
            async with _client() as c:
                r = await c.get(f"/artist/{artist_id}")
                r.raise_for_status()
                if r.json().get("monitored"):
                    return True
        except httpx.HTTPError:
            return False
    # Reverted (un-monitored, tracks purged, or artist flipped off).
    # Re-apply: ensure the artist is ON, then monitor just this one album.
    await set_artist_monitored(artist_id, True)
    return await _monitor_album(artist_id, album_id)


# --------------------------------------------------------------------------
# Request orchestration
# --------------------------------------------------------------------------
async def request_album(foreign_album_id: str) -> dict:
    """Ensure the album's artist exists, monitor the album, trigger search.
    Returns {lidarr_artist_id, lidarr_album_id, title, artist, imageUrl}.

    Never touches albums other than the one requested — safe for artists with
    a large or carefully curated discography."""
    info = await lookup_album_by_id(foreign_album_id)
    if not info:
        raise ValueError("Album not found in Lidarr metadata")
    artist = info.get("artist", {}) or {}
    foreign_artist_id = artist.get("foreignArtistId")

    existing = await find_library_artist(foreign_artist_id)
    if existing:
        artist_id = existing["id"]
        # Lidarr only includes albums of *monitored* artists in its
        # wanted/missing list (the pool Soularr reads). If the user had the
        # artist unmonitored (e.g. after carefully curating what to grab),
        # we must flip it ON so the newly-requested album is eligible.
        # set_artist_monitored is a plain PUT — it toggles only the top-level
        # flag; per-album monitoring is left untouched.
        await set_artist_monitored(artist_id, True)
    else:
        created = await add_artist(artist, monitor="none", search_missing=False)
        artist_id = created["id"]

    album = await _wait_for_album(artist_id, foreign_album_id)
    album_id = album["id"] if album else None
    if album_id:
        if not await _monitor_album(artist_id, album_id):
            raise RuntimeError(
                "Couldn't get Lidarr to monitor this album (its tracks never "
                "loaded). Tap Retry to try again."
            )
        if config.TRIGGER_ALBUM_SEARCH:
            await album_search([album_id])

    return {
        "lidarr_artist_id": artist_id,
        "lidarr_album_id": album_id,
        "title": info.get("title"),
        "artist": artist.get("artistName"),
        "imageUrl": _best_image(info.get("images", []), "album"),
    }


async def request_artist(foreign_artist_id: str) -> dict:
    """Add the artist with the whole discography monitored + searched."""
    info = await lookup_artist_by_id(foreign_artist_id)
    if not info:
        raise ValueError("Artist not found in Lidarr metadata")

    existing = await find_library_artist(foreign_artist_id)
    if existing:
        artist_id = existing["id"]
        await set_album_monitored(
            [a["id"] for a in await _albums_for_artist_id(artist_id)], True
        )
    else:
        created = await add_artist(info, monitor="all", search_missing=True)
        artist_id = created["id"]

    return {
        "lidarr_artist_id": artist_id,
        "lidarr_album_id": None,
        "title": info.get("artistName"),
        "artist": None,
        "imageUrl": _best_image(info.get("images", []), "artist"),
    }


# --------------------------------------------------------------------------
# Status (for /requests)
# --------------------------------------------------------------------------
def _status_from_percent(percent: Optional[float]) -> str:
    if not percent:
        return "pending"
    if percent >= 100:
        return "available"
    return "downloading"


async def request_status(lidarr_album_id: Optional[int],
                         lidarr_artist_id: Optional[int]) -> str:
    try:
        if lidarr_album_id:
            stats = await _album_stats(lidarr_album_id)
            return _status_from_percent(stats.get("percentOfTracks"))
        if lidarr_artist_id:
            async with _client() as c:
                r = await c.get(f"/artist/{lidarr_artist_id}")
                r.raise_for_status()
                stats = r.json().get("statistics", {}) or {}
                return _status_from_percent(stats.get("percentOfTracks"))
    except httpx.HTTPError:
        return "pending"
    return "pending"


# --------------------------------------------------------------------------
# Pipeline (detailed progress for the My Requests expand view)
# --------------------------------------------------------------------------
# Ordered stages the app renders as a stepper.
PIPELINE_STAGES = ["requested", "searching", "downloading", "importing", "available"]


# Short-lived cache for /album/{id}. request_status + request_pipeline both read
# the same album in one /api/requests pass, and the list is polled every few
# seconds, so a 3s TTL collapses the duplicate fetches without showing stale data.
_ALBUM_TTL = 3.0
_ALBUM_CACHE_MAX = 512
_album_cache: dict[int, tuple[float, dict]] = {}


async def _get_album(album_id: int) -> dict:
    now = time.monotonic()
    hit = _album_cache.get(album_id)
    if hit and now - hit[0] < _ALBUM_TTL:
        return hit[1]
    async with _client() as c:
        r = await c.get(f"/album/{album_id}")
        r.raise_for_status()
        data = r.json()
    # Bound growth: drop expired entries, then the oldest if still over cap.
    if len(_album_cache) >= _ALBUM_CACHE_MAX:
        for k in [k for k, (ts, _) in _album_cache.items() if now - ts >= _ALBUM_TTL]:
            del _album_cache[k]
        while len(_album_cache) >= _ALBUM_CACHE_MAX:
            del _album_cache[next(iter(_album_cache))]
    _album_cache[album_id] = (now, data)
    return data


async def _album_stats(album_id: int) -> dict:
    return (await _get_album(album_id)).get("statistics", {}) or {}


async def queue_index() -> dict[int, str]:
    """Map albumId -> Lidarr trackedDownloadState for everything in the queue,
    fetched ONCE so callers don't re-pull the whole queue per album. Returns {}
    on any upstream error (callers degrade to a coarse stage)."""
    try:
        async with _client() as c:
            r = await c.get("/queue", params={"pageSize": 100})
            r.raise_for_status()
            idx: dict[int, str] = {}
            for rec in r.json().get("records", []):
                aid = rec.get("albumId")
                if aid is not None:
                    state = rec.get("trackedDownloadState") or rec.get("status")
                    if state:
                        idx[aid] = state
            return idx
    except httpx.HTTPError:
        return {}


async def _queue_state_for_album(album_id: int) -> Optional[str]:
    """Single-album fallback used when no pre-fetched queue index is supplied."""
    return (await queue_index()).get(album_id)


def _build_pipeline(stage: str, percent: float, files: int, total: int,
                    detail: str, failed: bool = False, stuck: bool = False) -> dict:
    return {
        "stage": stage,
        "stageIndex": PIPELINE_STAGES.index(stage) if stage in PIPELINE_STAGES else 0,
        "stages": PIPELINE_STAGES,
        "percent": round(percent or 0, 1),
        "trackFiles": files,
        "trackCount": total,
        "detail": detail,
        "failed": failed,
        "stuck": stuck,
    }


# A request still "searching" after this long with no progress is flagged stuck.
_STUCK_AFTER_SECONDS = 25 * 60


def _is_stuck(created_at: Optional[str]) -> bool:
    if not created_at:
        return False
    from datetime import datetime, timezone
    try:
        started = datetime.fromisoformat(created_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - started).total_seconds() > _STUCK_AFTER_SECONDS
    except ValueError:
        return False


async def request_pipeline(lidarr_album_id: Optional[int],
                           lidarr_artist_id: Optional[int],
                           artist_name: Optional[str],
                           status: str,
                           created_at: Optional[str] = None,
                           queue_index: Optional[dict[int, str]] = None) -> dict:
    """Resolve where a request sits in the pipeline:
    requested -> searching -> downloading -> importing -> available.
    Best-effort: any upstream hiccup degrades gracefully to a coarse stage.

    Pass `queue_index` (from `queue_index()`) when resolving many requests at once
    so the Lidarr queue is fetched once for the whole batch, not once per album."""
    from . import slskd, soularr_cfg  # local import to avoid a cycle at module load

    if status == "error":
        return _build_pipeline("requested", 0, 0, 0,
                               "Request failed — tap Retry.", failed=True)
    if status == "failed":
        return _build_pipeline(
            "searching", 0, 0, 0,
            f"No source found after {config.SEARCH_GIVE_UP_ATTEMPTS} searches — tap Retry.",
            failed=True,
        )
    if not lidarr_album_id:
        return _build_pipeline("requested", 0, 0, 0, "Adding to Lidarr…")

    try:
        stats = await _album_stats(lidarr_album_id)
    except httpx.HTTPError:
        stats = {}
    pct = stats.get("percentOfTracks") or 0
    files = stats.get("trackFileCount") or 0
    total = stats.get("trackCount") or 0

    if pct >= 100:
        return _build_pipeline("available", 100, files, total,
                               f"In your library — {files}/{total} tracks.")

    # Live download view from slskd (the phase Lidarr's own queue can't see,
    # because Soularr downloads via slskd before handing off to Lidarr).
    prog = await slskd.artist_progress(artist_name)
    if prog and prog["active"]:
        return _build_pipeline(
            "downloading", pct, files, total,
            f"Downloading from slskd — {prog['files_done']}/{prog['files_total']} "
            f"files ({prog['percent']}%)"
            + (f" from {prog['username']}" if prog.get("username") else ""),
        )

    # Lidarr-tracked phase (import after slskd completes). Use the batch-fetched
    # queue index when one was supplied, else fall back to a single-album lookup.
    if queue_index is not None:
        qstate = queue_index.get(lidarr_album_id)
    else:
        try:
            qstate = await _queue_state_for_album(lidarr_album_id)
        except httpx.HTTPError:
            qstate = None
    if qstate:
        q = qstate.lower()
        if "import" in q:
            return _build_pipeline("importing", pct, files, total,
                                   "Importing into your library…")
        return _build_pipeline("downloading", pct, files, total,
                               "Downloading…")

    if 0 < files < total:
        return _build_pipeline("importing", pct, files, total,
                               f"Imported {files}/{total} tracks — finishing up.")

    # A failed import got the album denylisted by Soularr → needs a retry.
    if soularr_cfg.is_denylisted(lidarr_album_id):
        return _build_pipeline("searching", pct, files, total,
                               "A download failed to import — tap Retry.", failed=True)

    # Monitored + missing, nothing in flight: Soularr is/Will be searching slskd.
    if _is_stuck(created_at):
        return _build_pipeline(
            "searching", pct, files, total,
            "Still looking — no source found yet. Tap Retry to search again.",
            stuck=True,
        )
    return _build_pipeline("searching", pct, files, total,
                           "Searching Soulseek for a source…")
