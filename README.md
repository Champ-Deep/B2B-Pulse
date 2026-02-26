# B2B Pulse

**Social Engagement Automation Platform** — Automate LinkedIn likes, AI-generated comments, and coordinate team engagement across multiple organizations.

## Overview

B2B Pulse helps B2B teams amplify their social presence by automatically engaging with tracked LinkedIn company pages. When a tracked page publishes a new post, the platform polls for it, generates personalized AI comments per user, and orchestrates likes and comments with human-like stagger delays.

### Key Features

- **LinkedIn OAuth SSO** — Single sign-in with LinkedIn (also connects the automation integration)
- **Multi-org with sub-teams** — Independent organizations, each with sub-teams and team leaders
- **AI comment generation** — Two-pass LLM pipeline (generate + review) via OpenRouter
- **Engagement automation** — Auto-like and auto-comment with configurable stagger delays
- **Tracked pages** — Monitor LinkedIn company pages for new posts, auto-poll on schedule
- **Team invites** — Shareable invite links (org-wide or team-specific)
- **Audit log** — Full engagement history with CSV export
- **Platform admin** — Super-admin dashboard for managing all orgs

---

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Frontend   │────▶│   Backend    │────▶│ PostgreSQL  │
│  React/Vite  │     │   FastAPI    │     │    (DB)     │
│  Port 5173   │     │  Port 8000   │     │  Port 5432  │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                    ┌──────┴───────┐
                    │    Redis     │
                    │  Port 6379   │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │                         │
      ┌───────▼───────┐       ┌────────▼────────┐
      │ Celery Worker │       │  Celery Beat    │
      │ (engagement   │       │  (scheduled     │
      │  tasks)       │       │   polling)      │
      └───────────────┘       └─────────────────┘
```

| Service | Technology | Purpose |
|---------|-----------|---------|
| Frontend | React 18 + Vite + Tailwind CSS | SPA with LinkedIn OAuth, team management |
| Backend | FastAPI + SQLAlchemy async | REST API, OAuth flows, business logic |
| Database | PostgreSQL 16 | Persistent storage |
| Cache | Redis 7 | Celery broker, OAuth state, poll status |
| Worker | Celery | Async engagement tasks (like, comment) |
| Beat | Celery Beat | Scheduled page polling |

---

## Authentication Flow

B2B Pulse uses **LinkedIn OAuth 2.0 (OpenID Connect)** as the sole authentication method. A single sign-in both authenticates the user and connects their LinkedIn integration for automation.

```
User clicks "Sign in with LinkedIn"
  → Frontend calls GET /api/auth/linkedin
  → Backend returns LinkedIn OAuth URL (with state stored in Redis)
  → User authorizes on LinkedIn
  → LinkedIn redirects to GET /api/auth/linkedin/callback
  → Backend exchanges code for token, fetches profile
  → Creates/finds user, upserts IntegrationAccount with encrypted OAuth token
  → Redirects to frontend: /auth/callback#access_token=...&refresh_token=...
  → Frontend stores JWT tokens in localStorage
```

**Invite flow:** When a user has an invite code, it's embedded in the OAuth state. On callback, the user joins the invite's org (and team, if specified).

---

## Quick Start (Local Development)

### Prerequisites

- Docker & Docker Compose
- LinkedIn Developer App ([create one](https://developer.linkedin.com/))
- OpenRouter API key ([sign up](https://openrouter.ai/))

### Setup

```bash
# 1. Clone and enter project
git clone <repo-url> && cd b2b-pulse

# 2. Create env file
cp .env.example .env
# Edit .env — fill in JWT_SECRET, FERNET_KEY, LINKEDIN_CLIENT_ID/SECRET, OPENROUTER_API_KEY

# 3. Generate secrets
python -c "import secrets; print(secrets.token_urlsafe(48))"          # JWT_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FERNET_KEY

# 4. Start all services
docker compose up --build

# 5. Access
# Frontend:  http://localhost:5173
# Backend:   http://localhost:8000
# API docs:  http://localhost:8000/docs
```

### LinkedIn Developer App Setup

1. Go to [LinkedIn Developer Portal](https://developer.linkedin.com/) → Create App
2. Add products: **Sign In with LinkedIn using OpenID Connect** + **Share on LinkedIn**
3. Under **Auth** tab, add these redirect URIs:
   - `http://localhost:8000/api/auth/linkedin/callback` (login)
   - `http://localhost:8000/api/integrations/linkedin/callback` (integration reconnect)
4. Copy Client ID and Client Secret to your `.env`

---

## API Reference

Interactive API docs are available at `/docs` (Swagger UI) and `/redoc` (ReDoc).

### Auth (`/api/auth`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/auth/linkedin` | No | Get LinkedIn OAuth URL (optional `invite_code` query param) |
| GET | `/auth/linkedin/callback` | No | LinkedIn OAuth callback — creates/logs in user, redirects to frontend |
| POST | `/auth/refresh` | No | Exchange refresh token for new token pair |
| GET | `/auth/me` | Yes | Get current authenticated user |

### Integrations (`/api/integrations`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/integrations/linkedin/auth-url` | Yes | Get LinkedIn OAuth URL for reconnecting integration |
| GET | `/integrations/linkedin/callback` | No | LinkedIn integration OAuth callback |
| POST | `/integrations/linkedin/session-cookies` | Yes | Save li_at cookie for Playwright automation |
| GET | `/integrations/linkedin/session-status` | Yes | Check if user has valid session cookies |
| POST | `/integrations/linkedin/login-start` | Yes | Start Playwright-assisted LinkedIn login |
| POST | `/integrations/linkedin/login-verify` | Yes | Submit 2FA code for Playwright login |
| GET | `/integrations/meta/auth-url` | Yes | Get Meta OAuth URL |
| GET | `/integrations/meta/callback` | No | Meta OAuth callback |
| GET | `/integrations/status` | Yes | Get all integration statuses |

### Tracked Pages (`/api/tracked-pages`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/tracked-pages` | Yes | Add a tracked page (LinkedIn company URL) |
| GET | `/tracked-pages` | Yes | List all tracked pages for the org |
| DELETE | `/tracked-pages/{id}` | Yes | Remove a tracked page |
| POST | `/tracked-pages/{id}/subscribe` | Yes | Subscribe to a tracked page |
| DELETE | `/tracked-pages/{id}/subscribe` | Yes | Unsubscribe from a tracked page |
| PUT | `/tracked-pages/{id}/subscribe` | Yes | Update subscription settings |
| GET | `/tracked-pages/{id}/posts` | Yes | List posts for a tracked page |
| POST | `/tracked-pages/{id}/poll` | Yes | Trigger immediate poll for new posts |
| GET | `/tracked-pages/{id}/poll-status` | Yes | Get last poll status |
| POST | `/tracked-pages/import` | Yes | Bulk import pages from Excel/CSV |

### Organization (`/api/org`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/org/invites` | Admin | Create an invite link |
| GET | `/org/invites` | Admin | List all invites |
| DELETE | `/org/invites/{id}` | Admin | Revoke a pending invite |
| GET | `/org/invites/validate/{code}` | No | Validate an invite code (public) |
| GET | `/org/members` | Yes | List org members with integration status |
| DELETE | `/org/members/{id}` | Admin | Deactivate a member |

### Teams (`/api/org/teams`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/org/teams` | Admin | Create a new team |
| GET | `/org/teams` | Yes | List teams with member counts |
| PUT | `/org/teams/{id}` | Admin/Leader | Rename a team |
| DELETE | `/org/teams/{id}` | Admin | Delete team (unassigns members) |
| POST | `/org/teams/{id}/invite` | Admin/Leader | Create team-specific invite |
| PUT | `/org/teams/members/{user_id}/team` | Admin | Assign member to a team |

### Automation (`/api/automation`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/automation/settings` | Yes | Get automation settings |
| PUT | `/automation/settings` | Yes | Update automation settings |
| POST | `/automation/generate-comment` | Yes | Generate AI comment (preview) |
| GET | `/automation/avoid-phrases` | Yes | List custom avoid phrases |
| POST | `/automation/avoid-phrases` | Admin | Add avoid phrase |
| DELETE | `/automation/avoid-phrases/{id}` | Admin | Remove avoid phrase |

### Audit (`/api/audit`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/audit` | Yes | List audit logs (with filtering) |
| GET | `/audit/export` | Yes | Export audit logs as CSV |
| GET | `/audit/analytics/summary` | Yes | Engagement analytics summary |
| GET | `/audit/recent-activity` | Yes | Recent activity feed |

### Users (`/api/users`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/users/profile` | Yes | Get user profile |
| PUT | `/users/profile` | Yes | Update profile (markdown, tone) |
| PUT | `/users/role` | Admin | Update a member's role |

### Platform Admin (`/api/admin`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/orgs` | Platform Admin | List all organizations |
| GET | `/admin/orgs/{id}` | Platform Admin | Get org details |
| GET | `/admin/stats` | Platform Admin | Platform-wide statistics |

### Webhooks (`/api/webhooks`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/webhooks/whatsapp-link` | Internal | WhatsApp sidecar webhook |

---

## Data Models

### Roles

| Role | Permissions |
|------|------------|
| `owner` | Full org control, can manage all members and settings |
| `admin` | Same as owner, except cannot remove the owner |
| `team_leader` | Can manage their own team, generate team invites |
| `member` | Standard user — can engage, manage own profile |
| `analyst` | Read-only access to audit logs and analytics |

### Key Tables

- **users** — Org members with LinkedIn ID, role, team assignment
- **orgs** — Organizations (multi-tenant)
- **teams** — Sub-teams within orgs
- **org_invites** — Invite links (org-wide or team-specific)
- **tracked_pages** — LinkedIn company pages being monitored
- **tracked_page_subscriptions** — Per-user subscription to each page
- **posts** — Discovered posts from tracked pages
- **engagement_actions** — Likes/comments (queued, completed, failed)
- **integration_accounts** — OAuth tokens (encrypted at rest)
- **user_profiles** — Markdown bio, tone settings, automation config
- **audit_logs** — Audit trail of all actions
- **ai_avoid_phrases** — Custom phrases to exclude from AI comments

---

## Environment Variables

See [.env.example](.env.example) for the full list with setup instructions.

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string (use `postgresql+asyncpg://`) |
| `REDIS_URL` | Yes | Redis connection string |
| `JWT_SECRET` | Yes | Secret key for JWT signing |
| `FERNET_KEY` | Yes | Fernet encryption key for OAuth tokens at rest |
| `LINKEDIN_CLIENT_ID` | Yes | LinkedIn app Client ID |
| `LINKEDIN_CLIENT_SECRET` | Yes | LinkedIn app Client Secret |
| `LINKEDIN_REDIRECT_URI` | Yes | Integration OAuth callback URL |
| `LINKEDIN_AUTH_REDIRECT_URI` | Yes | Login OAuth callback URL |
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for AI comments |
| `CORS_ORIGINS` | Yes | Comma-separated frontend origins |
| `APP_ENV` | No | `development` or `production` (default: development) |
| `SENTRY_DSN` | No | Sentry DSN for error tracking |
| `META_APP_ID` | No | Meta/Facebook app ID |
| `META_APP_SECRET` | No | Meta/Facebook app secret |

---

## Railway Deployment

### Services to Deploy

| Service | Source | Build | Start Command |
|---------|--------|-------|---------------|
| Backend | `backend/` | Dockerfile | `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Celery Worker | `backend/` | Dockerfile | `celery -A app.workers.celery_app worker --loglevel=info --concurrency=4` |
| Celery Beat | `backend/` | Dockerfile | `celery -A app.workers.celery_app beat --loglevel=info` |
| Frontend | `frontend/` | Dockerfile.prod | nginx serves static build |
| PostgreSQL | Railway plugin | — | — |
| Redis | Railway plugin | — | — |

### Railway Setup Steps

1. Create a new Railway project
2. Add **PostgreSQL** and **Redis** plugins
3. Add services for Backend, Celery Worker, Celery Beat, Frontend
4. Set root directory for each service (`backend/` or `frontend/`)
5. Configure shared environment variables across backend services:
   - **Important:** Railway provides `DATABASE_URL` as `postgresql://...` — you must override it as `postgresql+asyncpg://...` for SQLAlchemy async
   - Set `REDIS_URL` from the Redis plugin's connection string
   - Set all LinkedIn, JWT, Fernet, and OpenRouter variables
   - Set `CORS_ORIGINS` to your frontend Railway URL
   - Set `LINKEDIN_AUTH_REDIRECT_URI` to `https://<backend-domain>/api/auth/linkedin/callback`
   - Set `LINKEDIN_REDIRECT_URI` to `https://<backend-domain>/api/integrations/linkedin/callback`
6. Set `APP_ENV=production` for all backend services
7. Frontend needs `VITE_API_URL=https://<backend-domain>/api` as build arg

### First Super Admin

After your first deployment and sign-in, promote yourself to platform admin:

```sql
UPDATE users SET is_platform_admin = true WHERE email = 'your@email.com';
```

---

## Project Structure

```
b2b-pulse/
├── backend/
│   ├── app/
│   │   ├── api/              # Route handlers
│   │   │   ├── auth.py       # LinkedIn OAuth login
│   │   │   ├── integrations.py # OAuth integrations
│   │   │   ├── org.py        # Org management, invites
│   │   │   ├── teams.py      # Sub-team CRUD
│   │   │   ├── admin.py      # Platform admin
│   │   │   ├── tracked_pages.py # Page tracking
│   │   │   ├── automation.py # Settings, AI comments
│   │   │   ├── audit.py      # Audit log & analytics
│   │   │   ├── users.py      # User profiles
│   │   │   └── webhooks.py   # External webhooks
│   │   ├── core/             # Auth, security, deps
│   │   ├── models/           # SQLAlchemy models
│   │   ├── schemas/          # Pydantic schemas
│   │   ├── services/         # Business logic (LLM)
│   │   ├── workers/          # Celery tasks
│   │   └── automation/       # Playwright actions
│   ├── alembic/              # Database migrations
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── pages/            # Route pages
│   │   ├── components/       # Shared components
│   │   ├── lib/              # Auth, types, utils
│   │   └── api/              # Axios client
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Development

```bash
# Run all services
docker compose up --build

# Run backend tests
docker compose exec backend pytest

# Run database migration
docker compose exec backend alembic upgrade head

# Create new migration
docker compose exec backend alembic revision --autogenerate -m "description"

# Lint backend
docker compose exec backend ruff check .

# Lint frontend
docker compose exec frontend npm run lint
```

---

## License

Proprietary. All rights reserved.
