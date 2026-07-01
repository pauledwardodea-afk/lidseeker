"""lidseeker backend package."""

# Single source of truth for the app version. Referenced by the FastAPI app
# (OpenAPI metadata) and surfaced at /api/health. Keep in step with the Android
# versionName and the release tag.
__version__ = "0.4.1-beta"
