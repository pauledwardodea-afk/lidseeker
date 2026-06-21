"""Read/write Soularr's config.ini (quality preference) and its failed-import
denylist (for retrying stuck albums). Soularr's config dir is mounted into this
container at /soularr.
"""
import configparser
import contextlib
import json
import logging
import os
import tempfile

from . import config

log = logging.getLogger("lidseeker")


def _atomic_write(path: str, write_fn) -> None:
    """Write via a temp file in the same dir + os.replace so a concurrent reader
    never sees a half-written file. `write_fn` receives the open text handle."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            write_fn(f)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

# Quality presets → Soularr's allowed_filetypes (most-preferred first).
QUALITY_PRESETS = {
    "mp3": "mp3 320,mp3",
    "flac": "flac,mp3 320,mp3",
}


def _read() -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    cp.read(config.SOULARR_CONFIG_PATH)
    return cp


def get_quality() -> str | None:
    """Return 'flac' if FLAC is accepted, else 'mp3'. Returns None when the
    Soularr adapter is off (the quality toggle only controls Soularr) so the app
    can hide the control."""
    if not config.SOULARR_ENABLED:
        return None
    try:
        cp = _read()
        types = cp.get("Search Settings", "allowed_filetypes", fallback="")
        return "flac" if "flac" in types.lower() else "mp3"
    except Exception as e:  # noqa: BLE001
        log.warning("read quality failed: %s", e)
        return "mp3"


def set_quality(quality: str) -> bool:
    """Set allowed_filetypes from a preset. Returns True if the file changed."""
    if not config.SOULARR_ENABLED:
        raise RuntimeError("Quality control is only available with the Soularr adapter")
    if quality not in QUALITY_PRESETS:
        raise ValueError("quality must be 'mp3' or 'flac'")
    cp = _read()
    if not cp.has_section("Search Settings"):
        raise RuntimeError("Soularr config not available")
    current = cp.get("Search Settings", "allowed_filetypes", fallback="")
    target = QUALITY_PRESETS[quality]
    if current == target:
        return False
    cp.set("Search Settings", "allowed_filetypes", target)
    _atomic_write(config.SOULARR_CONFIG_PATH, cp.write)
    return True


def clear_denylist_entry(album_id: int | None) -> bool:
    """Remove an album from Soularr's failed-import denylist so it retries.
    Returns True if an entry was removed."""
    if not album_id:
        return False
    path = config.SOULARR_DENYLIST_PATH
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            data = json.load(f)
    except (ValueError, OSError):
        return False
    key = str(album_id)
    if key in data:
        data.pop(key)
        _atomic_write(path, lambda f: json.dump(data, f, indent=2))
        return True
    return False


def is_denylisted(album_id: int | None) -> bool:
    if not config.SOULARR_ENABLED or not album_id:
        return False
    if not os.path.exists(config.SOULARR_DENYLIST_PATH):
        return False
    try:
        with open(config.SOULARR_DENYLIST_PATH) as f:
            return str(album_id) in json.load(f)
    except (ValueError, OSError):
        return False
