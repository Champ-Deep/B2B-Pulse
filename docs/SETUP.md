# AutoEngage Setup Guide

## Quick Start (Local Development)

```bash
# 1. Clone the repo and copy env file
cp .env.example .env
# Edit .env — fill in at minimum: JWT_SECRET, FERNET_KEY

# 2. Generate secrets
python -c "import secrets; print(secrets.token_urlsafe(48))"          # JWT_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FERNET_KEY

# 3. Start everything
docker compose up --build

# 4. Open http://localhost:5173 — sign up for an account
```

Services started by docker-compose:
| Service         | Port  | Description                    |
|-----------------|-------|--------------------------------|
| frontend        | 5173  | React (Vite) dev server        |
| backend         | 8000  | FastAPI + Alembic migrations   |
| celery-worker   | —     | Background task execution      |
| celery-beat     | —     | Cron scheduler (polls pages)   |
| postgres        | 5432  | PostgreSQL 16                  |
| redis           | 6379  | Redis 7 (Celery broker)        |
| whatsapp-sidecar| 3001  | WhatsApp Web.js webhook bridge |

---

## LinkedIn OAuth Setup

This is required for auto-liking to work via the REST API.

### 1. Create a LinkedIn Developer App
1. Go to [LinkedIn Developer Portal](https://developer.linkedin.com/)
2. Sign in and click **Create App**
3. Fill in:
   - **App name**: AutoEngage (or your choice)
   - **LinkedIn Page**: Select your company page (or create one)
   - **App logo**: Upload any logo
4. Click **Create app**

### 2. Add Required Products
In your app's page, go to the **Products** tab and request access to:
- **Sign In with LinkedIn using OpenID Connect** (grants `openid`, `profile`, `email`)
- **Share on LinkedIn** (grants `w_member_social` — needed for liking/commenting)

> Both products are usually auto-approved. If "Share on LinkedIn" requires review, you can start testing with the Sign In product while waiting.

### 3. Configure Redirect URI
1. Go to the **Auth** tab
2. Under **OAuth 2.0 settings**, add your redirect URI:
   - Local: `http://localhost:8000/api/integrations/linkedin/callback`
   - Production: `https://your-backend-domain.com/api/integrations/linkedin/callback`

### 4. Copy Credentials
From the **Auth** tab, copy:
- **Client ID** → `LINKEDIN_CLIENT_ID`
- **Client Secret** (click eye icon) → `LINKEDIN_CLIENT_SECRET`

Set `LINKEDIN_REDIRECT_URI` to match exactly what you added in step 3.

### 5. Verify Scopes
The app automatically requests these scopes during OAuth: `openid profile email w_member_social`

---

## Meta (Facebook/Instagram) OAuth Setup

Required for Facebook Page and Instagram engagement automation.

### 1. Create a Meta Developer App
1. Go to [Meta for Developers](https://developers.facebook.com/)
2. Click **Create App** → Choose **Business** type
3. Fill in the app name and contact email

### 2. Add Facebook Login
1. In your app dashboard, click **Add Product**
2. Select **Facebook Login** → **Set Up**
3. Go to **Facebook Login → Settings**
4. Add redirect URIs:
   - Local: `http://localhost:8000/api/integrations/meta/callback`
   - Production: `https://your-backend-domain.com/api/integrations/meta/callback`

### 3. Required Permissions
The following permissions are requested during OAuth:
- `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `pages_manage_engagement`
- `instagram_basic`, `instagram_manage_comments`, `instagram_content_publish`

> These require App Review for production use. For development/testing, you can use them with your own accounts.

### 4. Copy Credentials
From **Settings → Basic**:
- **App ID** → `META_APP_ID`
- **App Secret** → `META_APP_SECRET`

---

## OpenRouter (LLM) Setup

Required for AI-generated comments.

1. Sign up at [OpenRouter](https://openrouter.ai/)
2. Go to **Keys** → **Create Key**
3. Copy the key → `OPENROUTER_API_KEY`

Default models (configurable):
- Generation: `anthropic/claude-sonnet-4-5-20250929`
- Review: `anthropic/claude-haiku-4-5-20251001`

---

## Managed Cloud Deployment (Railway)

### Services to Create

In a Railway project, create these services:

| Railway Service    | Source           | Start Command |
|--------------------|------------------|---------------|
| **backend**        | `./backend` dir  | `alembic upgrade head && gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers 4` |
| **celery-worker**  | `./backend` dir  | `alembic upgrade head && celery -A app.workers.celery_app worker --loglevel=info --concurrency=4` |
| **celery-beat**    | `./backend` dir  | `alembic upgrade head && celery -A app.workers.celery_app beat --loglevel=info` |
| **frontend**       | `./frontend` dir | Uses `Dockerfile.prod` (nginx) |
| **whatsapp-sidecar** | `./whatsapp-sidecar` dir | `node index.js` |
| **PostgreSQL**     | Railway plugin   | Auto-managed |
| **Redis**          | Railway plugin   | Auto-managed |

### Environment Variables

Railway auto-injects `DATABASE_URL` and `REDIS_URL` for managed plugins.

> **Important**: Railway injects `DATABASE_URL` with `postgresql://` scheme. Our app needs `postgresql+asyncpg://`. Either:
> - Set `DATABASE_URL` manually with the `+asyncpg` driver prefix
> - Or add a startup script that transforms it (see `scripts/deploy.sh`)

All other env vars from `.env.example` must be set as Railway service variables (shared across backend, celery-worker, celery-beat).

### Production Checklist

- [ ] `APP_ENV=production`
- [ ] Generate unique `JWT_SECRET` (see command above)
- [ ] Generate unique `FERNET_KEY` (see command above)
- [ ] Set `CORS_ORIGINS` to your frontend domain (e.g., `https://app.yourdomain.com`)
- [ ] Set `LINKEDIN_REDIRECT_URI` to production backend URL
- [ ] Set `META_REDIRECT_URI` to production backend URL
- [ ] Set `VITE_API_URL` to production backend URL (used at frontend build time)
- [ ] Set `OPENROUTER_API_KEY`
- [ ] Configure LinkedIn and Meta OAuth apps with production redirect URIs

### Dockerfile Selection

For production deploys, use the `.prod` Dockerfiles:
- Backend: `backend/Dockerfile.prod` — multi-stage build with gunicorn, Playwright + Chromium pre-installed
- Frontend: `frontend/Dockerfile.prod` — builds static assets, serves via nginx

In Railway, set the Dockerfile path in each service's settings.

---

## Team Onboarding

Once deployed, each team member should:

1. **Sign up** at the frontend URL
2. **Go to Settings** → **Connect LinkedIn** (OAuth flow)
3. **Complete Onboarding** → Set comment tone/style preferences
4. **Go to Tracked Pages**:
   - Add individual LinkedIn/IG/FB page URLs, OR
   - Use **Import CSV/Excel** to bulk-add a watchlist
5. **Submit Posts** manually, or let the cron poller discover them automatically

### How Auto-Engagement Works

1. **Post Discovery**: Cron job polls tracked pages every 5 minutes (or WhatsApp webhook triggers instantly)
2. **Engagement Scheduling**: For each new post, creates staggered like/comment actions for all subscribed users
3. **Execution**: Celery worker executes actions via LinkedIn REST API (OAuth) with random delays
4. **Audit Trail**: All actions logged in the Audit page

---

## Troubleshooting

**LinkedIn OAuth returns error**: Ensure redirect URI in LinkedIn Developer Portal matches `LINKEDIN_REDIRECT_URI` exactly (including protocol and path).

**Likes failing with "No person_urn"**: The user needs to disconnect and reconnect LinkedIn in Settings. The person URN is captured during OAuth.

**Playwright scraping unreliable**: LinkedIn blocks unauthenticated browsers. The system prioritizes REST API for engagement. Scraping is mainly for post discovery — consider relying on manual post submission or WhatsApp webhook instead.

**Database migration errors**: Run `alembic upgrade head` manually. If tables already exist, you may need `alembic stamp head` to mark the migration as applied.
