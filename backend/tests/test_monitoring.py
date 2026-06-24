"""Verify the new minimal monitoring behaviour: _monitor_album touches
only the requested album, pagination works, and the request_album flow
leaves the rest of the discography alone."""

import asyncio
from unittest.mock import AsyncMock, patch

from app import lidarr


# ---------------------------------------------------------------------------
# _monitor_album — only touches the target album
# ---------------------------------------------------------------------------
def test_monitor_album_only_touches_target():
    """_monitor_album must call set_album_monitored with exactly the one album."""
    seen_calls = []

    async def fake_monitor(album_ids, monitored):
        seen_calls.append((list(album_ids), monitored))

    async def fake_refresh(artist_id):
        pass

    async def fake_get_album(album_id):
        return {"monitored": True, "statistics": {"trackCount": 12}}

    with (
        patch.object(lidarr, "set_album_monitored", fake_monitor),
        patch.object(lidarr, "refresh_artist", fake_refresh),
        patch.object(lidarr, "_get_album", fake_get_album),
    ):
        ok = asyncio.run(lidarr._monitor_album(artist_id=42, album_id=99))

    assert ok is True
    # Should be exactly two calls: monitor before refresh, monitor after refresh.
    assert seen_calls == [([99], True), ([99], True)], f"unexpected calls: {seen_calls}"


def test_monitor_album_returns_false_when_no_tracks():
    """If the album has 0 tracks after the sequence, return False."""

    async def fake_monitor(album_ids, monitored):
        pass

    async def fake_refresh(artist_id):
        pass

    async def fake_get_album(album_id):
        return {"monitored": True, "statistics": {"trackCount": 0}}

    with (
        patch.object(lidarr, "set_album_monitored", fake_monitor),
        patch.object(lidarr, "refresh_artist", fake_refresh),
        patch.object(lidarr, "_get_album", fake_get_album),
    ):
        ok = asyncio.run(lidarr._monitor_album(artist_id=42, album_id=99))

    assert ok is False


def test_monitor_album_returns_false_when_still_unmonitored():
    """If Lidarr rejected the monitor (album still unmonitored), return False."""

    async def fake_monitor(album_ids, monitored):
        pass

    async def fake_refresh(artist_id):
        pass

    async def fake_get_album(album_id):
        return {"monitored": False, "statistics": {"trackCount": 12}}

    with (
        patch.object(lidarr, "set_album_monitored", fake_monitor),
        patch.object(lidarr, "refresh_artist", fake_refresh),
        patch.object(lidarr, "_get_album", fake_get_album),
    ):
        ok = asyncio.run(lidarr._monitor_album(artist_id=42, album_id=99))

    assert ok is False


# ---------------------------------------------------------------------------
# _albums_for_artist_id — pagination
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Simulates Lidarr paginated /album responses."""
    def __init__(self, pages):
        self._pages = pages
        self._call = 0
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        self._call += 1
        return self._pages[self._call - 1]


class _PaginatedClient:
    """An async-context-manager client that returns paginated /album responses."""
    def __init__(self, pages):
        self._pages = pages
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, path, params=None):
        self.calls.append(dict(params or {}))
        page = int(params.get("page", 1)) - 1
        return _FakeResponse([self._pages[page]])


def test_albums_for_artist_id_paginates():
    """_albums_for_artist_id must paginate through all pages.
    A full page (len == page_size) triggers the next fetch;
    a partial page (len < page_size) is the last."""
    pages = [
        [{"id": i} for i in range(100)],   # page 1 — FULL (len 100 → fetch next)
        [{"id": 100}, {"id": 101}],          # page 2 — partial (len 2 → last)
    ]
    client = _PaginatedClient(pages)

    with patch.object(lidarr, "_client", lambda: client):
        albums = asyncio.run(lidarr._albums_for_artist_id(artist_id=1))

    assert len(albums) == 102
    assert albums[0]["id"] == 0 and albums[-1]["id"] == 101
    assert len(client.calls) == 2
    assert client.calls[0] == {"artistId": 1, "page": 1, "pageSize": 100}
    assert client.calls[1] == {"artistId": 1, "page": 2, "pageSize": 100}


def test_albums_for_artist_id_stops_on_empty_page():
    """Pagination stops when a page returns nothing (empty list)."""
    pages = [
        [{"id": i} for i in range(100)],   # page 1 — FULL (100 items → fetch next)
        [],                                  # page 2 — empty (stop)
    ]
    client = _PaginatedClient(pages)

    with patch.object(lidarr, "_client", lambda: client):
        albums = asyncio.run(lidarr._albums_for_artist_id(artist_id=1))

    assert len(albums) == 100
    assert len(client.calls) == 2  # page 1 returned 100, page 2 returned [] → stop


# ---------------------------------------------------------------------------
# request_album — existing artist flow
# ---------------------------------------------------------------------------
def test_request_album_existing_artist_sets_artist_monitored():
    """For an existing artist, request_album must flip the artist ON
    (so Lidarr includes its albums in the wanted/missing pool)
    WITHOUT touching any other albums' monitoring state."""

    calls = {}

    async def fake_lookup(foreign_id):
        return {
            "foreignAlbumId": "alb-1",
            "title": "Test Album",
            "artist": {"artistName": "Test Artist", "foreignArtistId": "art-1"},
            "images": [],
        }

    async def fake_find(foreign_id):
        return {"id": 10, "foreignArtistId": "art-1"}

    async def fake_set_artist_monitored(artist_id, monitored):
        calls["set_artist_monitored"] = (artist_id, monitored)

    async def fake_wait_for_album(artist_id, foreign_id):
        return {"id": 99, "foreignAlbumId": "alb-1"}

    async def fake_monitor_album(artist_id, album_id):
        calls["_monitor_album"] = (artist_id, album_id)
        return True

    async def fake_album_search(album_ids):
        calls["album_search"] = list(album_ids)

    with (
        patch.object(lidarr, "lookup_album_by_id", fake_lookup),
        patch.object(lidarr, "find_library_artist", fake_find),
        patch.object(lidarr, "set_artist_monitored", fake_set_artist_monitored),
        patch.object(lidarr, "_wait_for_album", fake_wait_for_album),
        patch.object(lidarr, "_monitor_album", fake_monitor_album),
        patch.object(lidarr, "album_search", fake_album_search),
        patch.object(lidarr.config, "TRIGGER_ALBUM_SEARCH", True),
    ):
        result = asyncio.run(lidarr.request_album("alb-1"))

    assert result["title"] == "Test Album"
    assert result["lidarr_artist_id"] == 10
    assert result["lidarr_album_id"] == 99
    # Artist must be set to monitored.
    assert calls["set_artist_monitored"] == (10, True)
    # _monitor_album should be called for just the one album.
    assert calls["_monitor_album"] == (10, 99)


def test_request_album_new_artist_does_not_call_set_artist_monitored():
    """For a new artist, add_artist already sets monitored=True so we don't
    need an extra set_artist_monitored call."""

    calls = {}

    async def fake_lookup(foreign_id):
        return {
            "foreignAlbumId": "alb-2",
            "title": "New Album",
            "artist": {"artistName": "New Artist", "foreignArtistId": "art-2"},
            "images": [],
        }

    async def fake_find(foreign_id):
        return None  # not in library

    async def fake_add_artist(lookup, monitor, search_missing):
        calls["add_artist"] = {"monitor": monitor}
        return {"id": 20}

    async def fake_set_artist_monitored(artist_id, monitored):
        calls["set_artist_monitored"] = (artist_id, monitored)

    async def fake_wait_for_album(artist_id, foreign_id):
        return {"id": 100, "foreignAlbumId": "alb-2"}

    async def fake_monitor_album(artist_id, album_id):
        calls["_monitor_album"] = (artist_id, album_id)
        return True

    async def fake_album_search(album_ids):
        pass

    with (
        patch.object(lidarr, "lookup_album_by_id", fake_lookup),
        patch.object(lidarr, "find_library_artist", fake_find),
        patch.object(lidarr, "add_artist", fake_add_artist),
        patch.object(lidarr, "set_artist_monitored", fake_set_artist_monitored),
        patch.object(lidarr, "_wait_for_album", fake_wait_for_album),
        patch.object(lidarr, "_monitor_album", fake_monitor_album),
        patch.object(lidarr, "album_search", fake_album_search),
        patch.object(lidarr.config, "TRIGGER_ALBUM_SEARCH", True),
    ):
        asyncio.run(lidarr.request_album("alb-2"))

    # set_artist_monitored should NOT be called for new artists (add_artist
    # already sets monitored=True).
    assert "set_artist_monitored" not in calls
    # _monitor_album called for the new album.
    assert calls["_monitor_album"] == (20, 100)


# ---------------------------------------------------------------------------
# ensure_album_monitored — re-asserts artist ON before monitor
# ---------------------------------------------------------------------------
def test_ensure_album_monitored_sets_artist_on_before_monitor():
    """When an album needs re-monitoring, ensure_album_monitored must flip
    the artist ON first, then call _monitor_album."""

    call_order = []

    async def fake_get_album(album_id):
        # Album is monitored but has no tracks → needs re-monitoring.
        return {"monitored": False, "statistics": {"trackCount": 0, "trackFileCount": 0}}

    async def fake_set_artist_monitored(artist_id, monitored):
        call_order.append(("set_artist", artist_id, monitored))

    async def fake_monitor_album(artist_id, album_id):
        call_order.append(("monitor_album", artist_id, album_id))
        return True

    with (
        patch.object(lidarr, "_get_album", fake_get_album),
        patch.object(lidarr, "set_artist_monitored", fake_set_artist_monitored),
        patch.object(lidarr, "_monitor_album", fake_monitor_album),
    ):
        ok = asyncio.run(lidarr.ensure_album_monitored(artist_id=5, album_id=88))

    assert ok is True
    # Artist ON must happen before _monitor_album.
    assert call_order == [
        ("set_artist", 5, True),
        ("monitor_album", 5, 88),
    ]
