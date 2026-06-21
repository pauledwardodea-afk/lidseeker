"""Environment-driven settings for the lidseeker backend."""
import logging
import os
import secrets

log = logging.getLogger("lidseeker")


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# --- Lidarr connection ---
LIDARR_URL = os.environ.get("LIDARR_URL", "http://localhost:8686").rstrip("/")
LIDARR_API_KEY = os.environ.get("LIDARR_API_KEY", "")

# Defaults used when adding artists to Lidarr.
ROOT_FOLDER_PATH = os.environ.get("ROOT_FOLDER_PATH", "/music")
QUALITY_PROFILE_ID = int(os.environ.get("QUALITY_PROFILE_ID", "1"))      # 1 = Any
METADATA_PROFILE_ID = int(os.environ.get("METADATA_PROFILE_ID", "1"))    # 1 = Standard

# Belt-and-braces: also fire Lidarr's own AlbumSearch command on request.
# Soularr picks up monitored+missing albums on its own cycle regardless.
TRIGGER_ALBUM_SEARCH = _bool("TRIGGER_ALBUM_SEARCH", True)

# --- MusicBrainz (song search) ---
# Track lookup isn't a Lidarr feature, so songs are searched via MusicBrainz.
# Their API requires a descriptive User-Agent identifying the app + a contact.
# Point MUSICBRAINZ_CONTACT at your own fork/instance when you deploy.
MUSICBRAINZ_CONTACT = os.environ.get(
    "MUSICBRAINZ_CONTACT", "https://github.com/IvoryCobra-VC/lidseeker"
)
MUSICBRAINZ_USER_AGENT = os.environ.get(
    "MUSICBRAINZ_USER_AGENT", f"lidseeker/1.0 ( {MUSICBRAINZ_CONTACT} )"
)

# --- Auth: bootstraps the first admin ---
# On first start these seed the initial admin account; add more users in-app
# (Settings -> Users). Existing single-user deployments just become the admin.
APP_USER = os.environ.get("APP_USER", "admin")
# Two ways to set the password:
#   APP_PASSWORD     — plaintext; hashed in-memory at startup (simplest, and safe
#                      to put straight in docker-compose — no '$' escaping needed).
#   APP_PASS_HASH    — a pre-computed bcrypt hash (takes precedence if both set).
#                      Generate one with:
#   docker run --rm ghcr.io/ivorycobra-vc/lidseeker \
#     python -c "import bcrypt; print(bcrypt.hashpw(b'yourpass', bcrypt.gensalt()).decode())"
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
APP_PASS_HASH = os.environ.get("APP_PASS_HASH", "")
if not APP_PASS_HASH and APP_PASSWORD:
    import bcrypt
    APP_PASS_HASH = bcrypt.hashpw(APP_PASSWORD.encode(), bcrypt.gensalt(rounds=12)).decode()
def _resolve_jwt_secret() -> str:
    """The JWT signing secret. If the operator set a real one, use it. Otherwise
    (unset, empty, or the placeholder) generate a random secret once and PERSIST
    it next to the database so it survives restarts — a baked-in default would let
    anyone forge admin tokens against a default install, and a per-boot random one
    would sign everyone out on every restart."""
    env = os.environ.get("JWT_SECRET", "")
    if env and env != "change-me":
        return env
    data_dir = os.path.dirname(os.environ.get("DB_PATH", "/data/lidseeker.db")) or "."
    secret_path = os.path.join(data_dir, ".jwt_secret")
    try:
        with open(secret_path) as f:
            existing = f.read().strip()
        if existing:
            return existing
    except OSError:
        pass
    secret = secrets.token_hex(32)
    try:
        os.makedirs(data_dir, exist_ok=True)
        # Write 0600 so other users on the host can't read the signing key.
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secret)
        log.info(
            "JWT_SECRET not set — generated and persisted a random secret at %s. "
            "Set JWT_SECRET explicitly if you run multiple instances.", secret_path
        )
    except OSError as e:
        # Can't persist (read-only data dir?) — fall back to an in-memory secret
        # so we still don't use the public placeholder. Tokens won't survive a
        # restart in this case.
        log.warning("couldn't persist generated JWT secret (%s); using a per-process one", e)
    return secret


JWT_SECRET = _resolve_jwt_secret()
JWT_TTL_HOURS = int(os.environ.get("JWT_TTL_HOURS", "48"))  # 2 days

# --- slskd (Soulseek daemon — for live download progress in the request pipeline) ---
SLSKD_URL = os.environ.get("SLSKD_URL", "http://localhost:5030").rstrip("/")
SLSKD_API_KEY = os.environ.get("SLSKD_API_KEY", "")

# --- Soularr control (force an immediate search by restarting its container) ---
# Routed through a locked-down nginx proxy (docker-proxy service) that only
# permits restarting this one container — the raw docker.sock is never exposed
# to this app.
SOULARR_CONTAINER = os.environ.get("SOULARR_CONTAINER", "soularr")
DOCKER_PROXY_URL = os.environ.get("DOCKER_PROXY_URL", "http://127.0.0.1:2375").rstrip("/")

# --- Push notifications via ntfy (fires when a request becomes available) ---
NTFY_URL = os.environ.get("NTFY_URL", "").rstrip("/")     # e.g. https://ntfy.sh or self-hosted
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")             # the topic the phone subscribes to
NOTIFY_POLL_SECONDS = int(os.environ.get("NOTIFY_POLL_SECONDS", "45"))

# How often to re-assert monitoring for pending requests. Lidarr's scheduled
# "refresh all artists" can un-monitor a freshly-added artist's album before
# Soularr grabs it, so we re-enforce until the download lands.
RECONCILE_POLL_SECONDS = int(os.environ.get("RECONCILE_POLL_SECONDS", "60"))

# --- Give-up policy ---
# If a request is still searching with no source found after this many Soularr
# search cycles, give up and mark it "failed" (red in the app, offers Retry).
# We count one fruitless attempt per SEARCH_ATTEMPT_INTERVAL_SECONDS (~one Soularr
# cycle) so the count tracks actual search rounds, not wall-clock alone.
SEARCH_GIVE_UP_ATTEMPTS = int(os.environ.get("SEARCH_GIVE_UP_ATTEMPTS", "5"))
SEARCH_ATTEMPT_INTERVAL_SECONDS = int(
    os.environ.get("SEARCH_ATTEMPT_INTERVAL_SECONDS", "300")
)

# --- Soularr config (for the quality toggle + retry/denylist handling) ---
SOULARR_CONFIG_PATH = os.environ.get("SOULARR_CONFIG_PATH", "/soularr/config.ini")
SOULARR_DENYLIST_PATH = os.environ.get(
    "SOULARR_DENYLIST_PATH", "/soularr/failed_imports.json"
)

# --- Download adapter selection ---
# lidseeker works against any Lidarr download client out of the box, reading
# progress from Lidarr's own queue ("Lidarr-native" mode). The optional Soularr +
# slskd (Soulseek) adapter adds live Soulseek progress, a FLAC/MP3 quality toggle,
# and a container-restart "search now". It's auto-enabled when an slskd API key is
# set AND Soularr's config is mounted/readable; set SOULARR_ENABLED to force it.
def _soularr_autodetect() -> bool:
    return bool(SLSKD_API_KEY) and os.path.exists(SOULARR_CONFIG_PATH)


SOULARR_ENABLED = _bool("SOULARR_ENABLED", _soularr_autodetect())

# --- Service links shown under a request in the app ---
# Comma-separated "Name|url" pairs, e.g. "Lidarr|http://100.x:8686,slskd|http://100.x:5030".
def _service_links() -> list[dict]:
    raw = os.environ.get("SERVICE_LINKS", "")
    out = []
    for part in raw.split(","):
        part = part.strip()
        if "|" in part:
            name, url = part.split("|", 1)
            if name.strip() and url.strip():
                out.append({"name": name.strip(), "url": url.strip()})
    return out


SERVICE_LINKS = _service_links()

# --- Storage ---
DB_PATH = os.environ.get("DB_PATH", "/data/lidseeker.db")

# --- Server ---
PORT = int(os.environ.get("PORT", "5056"))
