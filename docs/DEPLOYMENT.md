# Deploying B2B Pulse

Backend on your own VPS, frontend on a static host. That split is deliberate:
the backend holds encrypted LinkedIn session credentials and should stay on
infrastructure you control, while the frontend is a bundle of static files that
has no business being on the same box as the database.

Two things to know before you start, because they shape everything else:

1. **The `FERNET_KEY` is the most important value in the deployment.** It
   encrypts every connected LinkedIn credential. Lose it and every account must
   be reconnected by hand; leak it and anyone with the database can use your
   team's LinkedIn sessions.
2. **Redis persistence is a safety control, not just a durability nicety.** The
   rate limiter's sliding windows live there. Wiping Redis hands every account a
   fresh daily and weekly allowance — the one thing the caps exist to prevent.
   The compose file enables AOF for this reason; don't turn it off.

---

## 1. Backend on the VPS

### What you need

- A VPS with Docker and the Compose plugin. 2 vCPU / 4 GB is comfortable for
  five accounts; the workers idle almost all the time, and the peak is a
  Playwright fallback.
- A domain for the API (`api.example.com`) with an **A record already pointing
  at the VPS**. Caddy requests a certificate on first start, and issuance fails
  if the name doesn't resolve there yet.
- Ports 80 and 443 open. Nothing else — Postgres, Redis and the API are on the
  compose network only, deliberately.

### Configure

```bash
git clone <your-repo> b2b-pulse && cd b2b-pulse
cp .env.vps.example .env
```

Then fill in `.env`. Every value is commented; the ones that block startup are:

```bash
# Generate each of these fresh. Do not reuse them between environments.
python3 -c "import secrets; print(secrets.token_urlsafe(48))"                      # JWT_SECRET
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FERNET_KEY
openssl rand -base64 32 | tr -d '/+='                                              # POSTGRES_PASSWORD
```

Back up the `FERNET_KEY` somewhere off this server before you continue.

`CORS_ORIGINS` is the one people get wrong: it is the **frontend's** origin, not
the API's, with no trailing slash. Until it matches, the SPA loads fine and
every request fails.

### Start

```bash
docker compose -f docker-compose.vps.yml up -d --build
```

Migrations run as a gated one-shot: nothing that touches the schema starts until
`alembic upgrade head` exits 0. Watch it:

```bash
docker compose -f docker-compose.vps.yml logs -f migrate
docker compose -f docker-compose.vps.yml ps        # everything healthy?
curl https://api.example.com/health
```

If `migrate` fails, the API, worker and beat will not start at all. That is
intentional — a half-migrated schema is worse than an outage.

---

## 2. Frontend on a static host

The build is the same everywhere; only the deploy command differs. Two
environment variables must be set **at build time** — Vite inlines them into the
bundle, so setting them afterwards does nothing:

| Variable | Value |
|---|---|
| `VITE_API_URL` | `https://api.example.com/api` |
| `VITE_CLERK_PUBLISHABLE_KEY` | `pk_live_…` from the Clerk dashboard |

```bash
cd frontend
npm ci
npm run build       # → dist/
```

### Vercel

Connect the repo, set the root directory to `frontend`, and add both variables
under Settings → Environment Variables. `frontend/vercel.json` already handles
the SPA rewrite and asset caching.

### Netlify / Cloudflare Pages

Build command `npm run build`, publish directory `dist`, base directory
`frontend`. `frontend/public/_redirects` already handles the SPA fallback.

### Serving it from the VPS instead

Reasonable if you'd rather not involve a third party. Build locally, copy `dist/`
to the server, and add a second Caddy block:

```caddyfile
app.example.com {
	root * /srv/b2bpulse
	encode zstd gzip
	try_files {path} /index.html      # the SPA fallback — without it, deep links 404
	file_server
}
```

### The one thing every host gets wrong

The app is client-routed, so `/console` and `/accounts` are not files. A host
without a SPA fallback 404s on any deep link, which shows up as *"refreshing the
page logs me out"* — because Clerk's post-login redirect lands on a path the
host has never heard of. The configs above handle it; if you deploy somewhere
else, this is the setting to find.

---

## 3. Clerk

In the Clerk dashboard:

- Add the frontend origin (`https://app.example.com`) to allowed origins.
- Copy the **Frontend API** URL into `CLERK_ISSUER`, and its
  `/.well-known/jwks.json` into `CLERK_JWKS_URL`.
- Copy the publishable key into the frontend build.

Leave `CLERK_DEV_UNSAFE=false`. Set to `true` it accepts unsigned tokens, which
means anyone who can reach the API can mint themselves an admin session.

---

## 4. Before you connect a real account

The product has a preflight check for this. Run it from the UI (Accounts → the
account → Preflight) or:

```bash
curl -X POST https://api.example.com/api/warmup/accounts/<id>/preflight \
     -H "Authorization: Bearer <token>"
```

It is read-only — it verifies the credentials work and reads the account's
current state without performing any action.

What to expect once an account is connected, and why nothing appears to happen
at first:

| Days | What runs | Invitations |
|---|---|---|
| 1–2 | Signing in, a handful of likes | none |
| 3–5 | Likes and follows | none |
| 6–9 | First comments | none |
| 10–14 | First posts | none |
| 15–21 | First connection requests, small volume | ~5–10/day |
| 22+ | Full programme | up to the tier cap |

A newly connected account will look idle for the first day or two. That is the
warm-up programme working, not a broken deployment. The ramp exists because an
account whose first action is an AI comment on a company post is the profile
that gets restricted.

**Watch the acceptance rate above all else.** Below 15% LinkedIn treats an
account as spam regardless of how modest the volume is, and roughly a quarter of
restricted accounts were inside the published limits the whole time. The
governor throttles automatically, but a sustained low rate means the targeting
is wrong, and no amount of throttling fixes bad targeting.

---

## 5. Operating it

### The stop button

```bash
curl -X POST https://api.example.com/api/console/steer \
     -H "Authorization: Bearer <token>" \
     -d '{"pause": true, "reason": "investigating a warning"}'
```

This reaches every account immediately, including work already queued. Use it
the moment LinkedIn shows anyone a warning or a checkpoint.

### Day to day

```bash
docker compose -f docker-compose.vps.yml logs -f worker
docker compose -f docker-compose.vps.yml logs -f beat
docker compose -f docker-compose.vps.yml exec postgres \
  pg_dump -U b2bpulse b2bpulse | gzip > backup-$(date +%F).sql.gz
```

Back up the database *and* keep the `FERNET_KEY` separately. A database backup
without the key is unreadable, which is the intended behaviour and a nasty
surprise if you discover it during a restore.

### Upgrading

```bash
git pull
docker compose -f docker-compose.vps.yml up -d --build
```

Migrations run automatically and gate the rest of the stack. If a migration
fails, the old containers keep running and the new ones don't start.

### Scaling

`worker` can be scaled; **`beat` must not be.** Two beat containers double every
scheduled task, which means each account plans and executes its day twice — over
its caps, from the product's own scheduler.

```bash
docker compose -f docker-compose.vps.yml up -d --scale worker=2
```

Raising worker concurrency does not raise throughput: the per-account caps bound
real volume, so more workers only make the limiter refuse more often.

---

## Troubleshooting

**`migrate` exits non-zero.** Read its log. The usual cause on a re-deploy is a
migration that ran halfway; `docker compose ... exec postgres psql -U b2bpulse
-c "select * from alembic_version"` tells you where it stopped.

**Caddy can't get a certificate.** The A record doesn't point here yet, or port
80 is closed. Both are required — Let's Encrypt validates over HTTP.

**The SPA loads but every request fails.** `CORS_ORIGINS` doesn't match the
frontend origin exactly. Check for a trailing slash and for `http` vs `https`.

**Everything is healthy but no account does anything.** Expected for the first
day or two (see the ramp above). After that, check `/api/console/overview` —
`paused`, the stage, and the health verdict are all reported per account, with a
reason.

**`npm ci` fails on the static host.** The lockfile is out of sync with
`package.json`. Run `npm install` locally and commit the updated
`package-lock.json`.
