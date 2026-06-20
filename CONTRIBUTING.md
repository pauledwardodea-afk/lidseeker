# Contributing to Lidseeker

Thanks for your interest! Lidseeker is a small, beta, self-hosted project — issues and PRs are
welcome.

## Project layout

- `backend/` — FastAPI service (Python 3.12) + the bundled web UI (`backend/web/`, React + Vite + TS).
- `android/` — Kotlin / Jetpack Compose app.

The web UI and Android app are two clients for the same `/api` HTTP surface.

## Dev setup

**Backend**
```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt ruff pytest
cp .env.example .env        # set LIDARR_URL/KEY, APP_PASSWORD, JWT_SECRET
uvicorn app.main:app --reload --port 5056
```

**Web UI** (proxies `/api` to the backend on :5056)
```bash
cd backend/web
npm install
npm run dev
```

**Android**
```bash
cd android
./gradlew assembleDebug
```

## Before you open a PR

CI must pass — it mirrors these checks, so run them locally:

```bash
# backend
cd backend && ruff check app tests && pytest -q
# web
cd backend/web && npx tsc --noEmit && npm run build
# android
cd android && ./gradlew assembleDebug
```

- Match the surrounding code style. Python is linted with `ruff`; the web app is type-checked with `tsc`.
- Add or update a test when you change backend behaviour.
- Keep PRs focused, and describe what changed and why.

## Reporting bugs / requesting features

Open an issue with the templates provided. For anything security-sensitive, see
[SECURITY.md](SECURITY.md) instead.
