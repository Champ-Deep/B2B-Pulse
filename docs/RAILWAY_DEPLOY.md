# B2B Pulse — Railway Deployment Playbook

This document is the authoritative guide for deploying B2B Pulse on Railway.
It was built iteratively from real deployment failures; each section explains
not just what to do but why the failure happened.

---

## Table of Contents

1. [Pre-Deploy Checklist](#pre-deploy-checklist)
2. [Railway Service Setup](#railway-service-setup)
3. [Required Environment Variables](#required-environment-variables)
4. [Start Commands Per Service](#start-commands-per-service)
5. [Post-Deploy Validation](#post-deploy-validation)
6. [Troubleshooting Index](#troubleshooting-index)
7. [Problems Log](#problems-log)

---

## Pre-Deploy Checklist

Complete every item before triggering a deploy. Skipping any one of these has
caused a real production failure (see Problems Log below).

### Code / Repo
- [ ] Root `Dockerfile` exists at repo root (Railway looks here when no Root Directory is set)
- [ ] Root `railway.toml` exists and sets `dockerfilePath = "Dockerfile"`
- [ ] `backend/celerybeat-schedule` is NOT in git (`git ls-files | grep celerybeat` should return nothing)
- [ ] `PLAYWRIGHT_BROWSERS_PATH` is set to a world-readable path in the Dockerfile (`/opt/playwright-browsers`)

### Railway Project
- [ ] PostgreSQL plugin added → provides `DATABASE_URL` automatically
- [ ] Redis plugin added → provides `REDIS_URL` automatically
- [ ] All required variables set (see table below)
- [ ] `APP_ENV=production` set on all backend services
- [ ] `VITE_API_URL` set as a **build-time** variable on the frontend service

---

## Railway Service Setup

Create one Railway service per row. For services that share the same repo,
leave "Root Directory" blank so Railway reads the root `railway.toml`.

| Service | Source | Root Dir | Dockerfile | Start Command |
|---------|--------|----------|------------|---------------|
| **backend** | repo | *(blank)* | `Dockerfile` (root) | `railway-start` (via `railway.toml`) |
| **celery-worker** | repo | `backend` | `Dockerfile.prod` | `celery -A app.workers.celery_app worker --loglevel=info --concurrency=4` |
| **celery-beat** | repo | `backend` | `Dockerfile.prod` | `celery -A app.workers.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule --pidfile=/tmp/celerybeat.pid` |
| **frontend** | repo | `frontend` | `Dockerfile.prod` | *(uses nginx CMD, no override)* |
| **PostgreSQL** | plugin | — | — | auto |
| **Redis** | plugin | — | — | auto |

> Only the **backend** service runs `alembic upgrade head` (inside `railway-start`).
> Worker and Beat must NOT run migrations — parallel migration runs race and fail.

---

## Required Environment Variables

Set these in Railway → each backend service → Variables.
Use a **Shared Variable** group for variables common to backend, worker, and beat.

| Variable | Required | Source / How to get |
|----------|----------|---------------------|
| `DATABASE_URL` | **Yes** | Auto-set by PostgreSQL plugin. Must start with `postgresql+asyncpg://` — the app's `config.py` rewrites `postgresql://` automatically, no manual fix needed. |
| `JWT_SECRET` | **Yes** | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `FERNET_KEY` | **Yes** | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `REDIS_URL` | **Yes** | Auto-set by Redis plugin |
| `APP_ENV` | **Yes** | `production` |
| `CORS_ORIGINS` | **Yes** | Your frontend Railway domain, e.g. `https://b2bpulse.up.railway.app` |
| `LINKEDIN_CLIENT_ID` | **Yes** | LinkedIn Developer Portal → App → Auth |
| `LINKEDIN_CLIENT_SECRET` | **Yes** | LinkedIn Developer Portal → App → Auth |
| `LINKEDIN_AUTH_REDIRECT_URI` | **Yes** | `https://<backend-domain>/api/auth/linkedin/callback` |
| `LINKEDIN_REDIRECT_URI` | **Yes** | `https://<backend-domain>/api/integrations/linkedin/callback` |
| `OPENROUTER_API_KEY` | **Yes** | https://openrouter.ai/keys |
| `SENTRY_DSN` | No | Sentry project DSN |
| `META_APP_ID` | No | Meta Developer Portal |
| `META_APP_SECRET` | No | Meta Developer Portal |
| `VITE_API_URL` | **Frontend only** | Set as build-time variable: `https://<backend-domain>/api` |

> `JWT_SECRET` and `FERNET_KEY` are secrets — generate them once and never
> change them in production (changing them invalidates all existing sessions and
> encrypted OAuth tokens).

---

## Start Commands Per Service

### Backend (web service)
```
railway-start
```
`railway-start` is a script baked into the image (`scripts/railway-start.sh`).
It performs a pre-flight env-var check, runs `alembic upgrade head`, then
starts gunicorn. If any required variable is missing it prints a clear message
and exits with code 1 — no cryptic Pydantic traceback.

### Celery Worker
```
celery -A app.workers.celery_app worker --loglevel=info --concurrency=4
```

### Celery Beat
```
celery -A app.workers.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule --pidfile=/tmp/celerybeat.pid
```
`/tmp` is writeable by the container without root. The schedule file must NOT
be persisted across deploys (hence `/tmp`, not `/app`).

### Frontend
No start command override. `nginx` is started by the image's `CMD` and listens
on `$PORT` via `envsubst` rendering of `nginx.conf.template`.

---

## Post-Deploy Validation

After Railway shows all services as healthy:

1. Open `https://<backend-domain>/health` — should return `{"status":"healthy"}`
2. Open `https://<backend-domain>/docs` — Swagger UI should load
3. Open `https://<frontend-domain>` — login page should load
4. Click "Sign in with LinkedIn" — should redirect to LinkedIn OAuth
5. In Railway → backend service → Logs, confirm: `Pre-flight OK` and `Running: alembic upgrade head`
6. In Railway → celery-worker → Logs, confirm: `celery@... ready.`

### First Super Admin
After your first sign-in, promote yourself to platform admin via the Railway
PostgreSQL plugin's query console:
```sql
UPDATE users SET is_platform_admin = true WHERE email = 'your@email.com';
```

---

## Troubleshooting Index

| Error in logs | Root cause | Fix |
|---------------|------------|-----|
| `Dockerfile 'Dockerfile' does not exist` | No `Dockerfile` at repo root; Railway uses root as build context when no Root Directory is set | Create `/Dockerfile` pointing to backend build |
| `ValidationError: database_url / jwt_secret / fernet_key — Field required` | Environment variables not set in Railway Variables | Add `DATABASE_URL`, `JWT_SECRET`, `FERNET_KEY` in Railway → Variables |
| `could not connect to server: Connection refused` | `DATABASE_URL` set but PostgreSQL plugin not added, or wrong host | Add Railway PostgreSQL plugin; it injects `DATABASE_URL` automatically |
| `playwright._impl._errors.Error: Executable doesn't exist` | Playwright browsers installed to `/root/.cache` (root-only); app runs as non-root `appuser` | Set `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers` and `chmod -R a+rX` in Dockerfile |
| `celery beat: ERROR: …` on restart | Stale `celerybeat-schedule` DBM file baked into image from git | Remove from git (`git rm --cached`), write schedule to `/tmp` at runtime |
| Alembic race: `multiple heads` or `target database is not up to date` | Worker/beat services also running `alembic upgrade head` | Only the web (backend) service runs migrations |
| `npx: command not found` or `tsx: not found` | WhatsApp sidecar Dockerfile uses `npm ci --omit=dev`, which strips `tsx` from devDependencies | Use plain `npm install` (no `--omit=dev`) in sidecar Dockerfile |
| Frontend loads but API calls 404 | `VITE_API_URL` not set at **build time** | Set `VITE_API_URL` as a Railway build variable (not a runtime variable) |

---

## Problems Log

Ordered chronologically. Each entry is a real failure encountered while
deploying B2B Pulse on Railway.

---

### Problem 1 — "Dockerfile does not exist"
**Date**: 2026-04-23  
**Symptom**: Railway build fails immediately: `Dockerfile 'Dockerfile' does not exist`  
**Root cause**: The repo is a monorepo. All Dockerfiles live in subdirectories
(`backend/`, `frontend/`, `whatsapp-sidecar/`). Railway, with no Root Directory
configured, looks for a `Dockerfile` at the repo root and finds nothing.  
**Fix applied**:
- Created `/Dockerfile` (root-level, build context = repo root).
- Adjusted both `COPY` instructions to use `backend/` prefix:
  - `COPY backend/pyproject.toml .`
  - `COPY backend/ .`
- Created `/railway.toml` pointing at this Dockerfile.
- `backend/Dockerfile.prod` and `backend/railway.toml` are kept for
  `docker-compose.prod.yml` where context is `./backend`.

---

### Problem 2 — `DATABASE_URL` scheme mismatch (asyncpg)
**Date**: 2026-04-23  
**Symptom**: App or Alembic crashes with `could not translate host name` or
asyncpg dialect error.  
**Root cause**: Railway's PostgreSQL plugin injects `DATABASE_URL=postgresql://…`
(sync driver prefix). SQLAlchemy's async engine and Alembic's async env require
`postgresql+asyncpg://`.  
**Fix applied**:
- Added `field_validator("database_url")` in `backend/app/config.py` that
  rewrites both `postgresql://` and `postgres://` to `postgresql+asyncpg://`.
- Alembic's `env.py` reads from `app.config.settings`, so it gets the corrected
  URL automatically. No manual variable override needed.

---

### Problem 3 — Playwright Chromium not found at runtime
**Date**: 2026-04-23  
**Symptom**: `playwright._impl._errors.Error: Executable doesn't exist at
/root/.cache/ms-playwright/…`  
**Root cause**: `Dockerfile.prod` ran `playwright install chromium` as root
(browsers go to `/root/.cache/ms-playwright`), then switched to non-root
`appuser` who cannot read `/root/.cache`.  
**Fix applied** (in both `Dockerfile` and `backend/Dockerfile.prod`):
```dockerfile
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" \
    && playwright install chromium --with-deps \
    && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"
```

---

### Problem 4 — Celery Beat crashes on restart (stale schedule DBM)
**Date**: 2026-04-23  
**Symptom**: Celery Beat fails to start or corrupts its schedule after deploy.  
**Root cause**: `backend/celerybeat-schedule` (a GNU dbm binary state file) was
committed to git. It gets baked into every Docker image. When Beat tries to
write to a pre-existing dbm file it did not create, it can crash or corrupt.  
**Fix applied**:
- `git rm --cached backend/celerybeat-schedule` — removed from git history.
- Added to `.gitignore`: `backend/celerybeat-schedule`, `backend/celerybeat-schedule.*`, `celerybeat.pid`.
- Beat start command writes to `/tmp/celerybeat-schedule` (not `/app`).

---

### Problem 5 — Docs said "node index.js" for WhatsApp sidecar
**Date**: 2026-04-23  
**Symptom**: Sidecar service would fail with `node: can't open file 'index.js'`  
**Root cause**: `docs/SETUP.md` listed `node index.js` as the sidecar start
command. There is no `index.js` — only `src/index.ts`. The Dockerfile correctly
uses `npx tsx src/index.ts`.  
**Fix applied**: Updated `docs/SETUP.md` and `README.md` to reflect `npx tsx src/index.ts`.

---

### Problem 6 — Missing environment variables crash with opaque Pydantic traceback
**Date**: 2026-04-23  
**Symptom**: `alembic upgrade head` (run at startup) imports `app.config`, which
instantiates Pydantic's `Settings`. Missing `DATABASE_URL`, `JWT_SECRET`, and
`FERNET_KEY` cause a `ValidationError` with a multi-line Python traceback. The
process exits immediately. Railway health check then reports "service
unavailable" — the real cause (missing env vars) is buried in the logs.  
**Fix applied**:
- Created `scripts/railway-start.sh` — a shell script that:
  1. Checks for missing required variables before running anything.
  2. Prints a clear, human-readable list of what is missing and how to fix it.
  3. Runs `alembic upgrade head` only if all required vars are present.
  4. Starts gunicorn with `exec` so it replaces the shell as PID 1.
- Root `Dockerfile` COPYs this script to `/usr/local/bin/railway-start` and uses it as CMD.
- Root `railway.toml` sets `startCommand = "railway-start"`.
- **Action required by user**: Add `DATABASE_URL` (auto from PostgreSQL plugin),
  `JWT_SECRET`, and `FERNET_KEY` to the Railway service's Variables tab.
