# Claude working notes — B2B-Pulse / ChampMail

This file gives future Claude sessions the context they need to work effectively in this repo without re-discovering everything.

## What this project is

ChampMail (repo name: B2B-Pulse-new) is a LinkedIn engagement automation tool. Core flow:

1. User signs up + connects their LinkedIn session.
2. User sets a tone-of-voice profile (Onboarding).
3. User adds LinkedIn post/profile URLs to track (TrackedPages).
4. Celery workers use Playwright + the user's stored session cookie to auto-like and auto-comment on tracked content, in the user's voice, within rate/stagger limits.

Stack: FastAPI + SQLAlchemy (async) + Postgres + Redis + Celery (worker + beat) + Playwright + a React/Vite frontend. Everything runs via `docker compose`.

## Repo layout

```
backend/                FastAPI app
  app/
    api/                HTTP routers (auth.py, integrations.py, users.py, ...)
    automation/         linkedin_actions.py — Playwright-driven like/comment
    core/               security (fernet, JWT), dependencies (get_current_user)
    models/             SQLAlchemy models (integration.py → IntegrationAccount)
    workers/            Celery app + tasks (engagement_tasks, staggering)
    config.py           Settings (Pydantic) read from .env
    main.py             FastAPI entrypoint, CORS, router registration
frontend/               Vite + React, routes in src/lib/routes.ts
linkedin-extension/     (being built) Chrome MV3 extension that syncs li_at cookie
whatsapp-sidecar/       Node service for WhatsApp side of things (separate)
docs/
  PLAN.md               Current ideation & implementation plan — READ FIRST
  SETUP.md              Local setup instructions
docker-compose.yml      Dev stack (7 services)
docker-compose.prod.yml Production variant
```

## Ports (dev)

- Frontend (Vite dev): `http://localhost:5173`
- Backend: host `8001` → container `8000` (note the mapping)
- Postgres: `5433` (host) → `5432` (container)
- Redis: `6379`
- WhatsApp sidecar: `3001`

## Key environment variables (.env)

- `JWT_SECRET` — access/refresh token signing
- `FERNET_KEY` — encrypts `IntegrationAccount.access_token`, `refresh_token`, `session_cookies`. **Not** `ENCRYPTION_KEY`, despite the name
- `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` — OAuth (identity only, does *not* give automation cookies)
- `LINKEDIN_REDIRECT_URI` / `LINKEDIN_AUTH_REDIRECT_URI` — OAuth callbacks
- `CORS_ORIGINS` — CSV of allowed origins; extension flow needs `chrome-extension://*` added (use `allow_origin_regex` on the FastAPI side since wildcards don't work with `allow_origins`)
- `BROWSER_PROXY_URL` — optional residential proxy for Playwright on the server path

## Architectural truths to remember

1. **LinkedIn OAuth cannot authorize automation.** It's identity-only. The worker needs the `li_at` session cookie, which comes from one of three paths (see `docs/PLAN.md`): extension (primary), Playwright-on-server (fallback), DevTools paste (advanced).
2. **All three paths write the same schema** into `IntegrationAccount.session_cookies` (encrypted JSON with `li_at`, `JSESSIONID?`, `captured_at`, `source`). The worker doesn't branch on source.
3. **`session_expires_at` is 30 days**, not 365. LinkedIn's real `li_at` lifetime is ~30 days; the old 365 was optimistic.
4. **Extension authenticates via pairing token**, not the user's JWT. Flow: web app calls `POST /integrations/extension/pair` with JWT → gets short-lived token → sends to extension via `chrome.runtime.sendMessage` → extension uses `X-Pairing-Token` header on subsequent cookie posts.
5. **Duplicate OAuth callback exists** in both `auth.py` (identity+user creation) and `integrations.py` (reconnect). Dead code around "capture session cookies from OAuth response" exists at `auth.py:223,232` — OAuth does not return cookies, this never worked. PLAN.md calls for deleting it; don't resurrect it.
6. **Frontend API URL is build-time via `VITE_API_URL`.** The existing compose has a bug where it sets this to `:8000` while backend runs on `:8001`. Fix in Phase 1.

## Where things live (quick reference)

- Cookie encryption: `backend/app/core/security.py` — `encrypt_value`, `decrypt_value`, `fernet` instance.
- Auth dependency: `backend/app/core/dependencies.py` — `get_current_user` (HTTPBearer).
- Integration model: `backend/app/models/integration.py` — `IntegrationAccount`.
- Cookie write endpoints: `backend/app/api/integrations.py` — `/linkedin/session-cookies` (paste), `/linkedin/login-start` + `/login-verify` (Playwright), soon `/extension/*` (new).
- Worker-side cookie use: `backend/app/automation/linkedin_actions.py` — `_get_user_cookies`, `check_session_valid`, `like_post`, `comment_on_post`.
- Frontend routes: `frontend/src/lib/routes.ts`.
- Onboarding page (existing): `frontend/src/pages/Onboarding.tsx`.
- Settings page (existing): `frontend/src/pages/AutomationSettings.tsx`.

## Current branch + recent work

- Branch: `feat/realtime-websockets-1370144387161798749` — recent commits added a WebSockets-based realtime campaign feed. Unrelated to the LinkedIn auth work, but live in this branch.
- The LinkedIn auth redesign work tracked in `docs/PLAN.md` is starting now.

## Conventions

- Backend is async SQLAlchemy (`AsyncSession`). Never use the sync session.
- Pydantic models for request/response, not raw dicts.
- New celery tasks: register in `backend/app/workers/celery_app.py`.
- Frontend fetches go through a thin API client that attaches the JWT from `localStorage`; don't duplicate auth logic per page.
- No new `.md` files except on explicit request (PLAN.md and this file were both requested).

## Known gotchas

- Playwright login has a 3/hour rate limit per user in `integrations.py` — surface it to the UI if you touch that code path.
- `b2b-pulse-new-postgres-1` is this project's own postgres container from compose; if it's "stuck" running alone, compose will happily reuse it on `up`.
- Frontend's `VITE_API_URL` is captured at build time. Changing `.env` requires a rebuild of the frontend container for the change to take effect.
- CORS: FastAPI's `CORSMiddleware` doesn't accept wildcard schemes like `chrome-extension://*` via `allow_origins`. Use `allow_origin_regex` instead.

## When in doubt

Read `docs/PLAN.md` first. It captures the active design decisions and the reasoning behind them.
