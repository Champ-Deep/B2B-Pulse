# Deployment runbook

**Who this is for:** whoever is standing the system up for the first time. You
do not need to know how the product works internally. Follow it top to bottom.

**Time:** about 90 minutes, most of it waiting for things to build.

---

## Read this part first

This system logs into real LinkedIn accounts belonging to real colleagues and
acts as them. LinkedIn restricts accounts it thinks are automated, and a
restriction is not something you can appeal your way out of quickly.

So there is one hard rule:

> ### You do not connect any LinkedIn account.
>
> Not a real one, not a test one, not your own. You stand up the
> infrastructure, run the verification script, and hand back.

Everything in this runbook is safe. Nothing here touches LinkedIn. The moment
something would, the runbook stops and tells you to hand back.

Two other things worth knowing before you start, because they change how
careful you are with two specific values:

- **`FERNET_KEY`** encrypts every stored LinkedIn credential. If it is lost,
  every account has to be reconnected by hand. If it leaks, whoever has it can
  use the team's LinkedIn sessions. Treat it like a production database
  password, because it is worse than one.
- **`.env` must never be committed.** It is already in `.gitignore` and the
  verification script checks it. If you ever see `.env` in `git status`, stop
  and tell whoever handed you this.

If anything in this runbook does not match what you see, **stop and ask**.
Guessing is the failure mode this document exists to prevent.

---

## What you are building

```
   Browser
      │
      ├──────────────► app.<domain>          Vercel (static files)
      │                     │
      │                     │ API calls
      ▼                     ▼
   Clerk              api.<domain>           Your VPS
   (login)                  │
                            ├── Caddy        TLS, the only open port
                            ├── api          the application
                            ├── worker       does the scheduled work
                            ├── beat         decides when work happens
                            ├── postgres     data
                            └── redis        rate-limit counters
```

Two deployments: a **backend** on a VPS you control, and a **frontend** on
Vercel. They are independent — you can redeploy either without touching the
other.

---

## Before you start: what you need from your manager

You cannot finish without these. Ask for all of them at once rather than
discovering them one at a time.

| # | What | Notes |
|---|---|---|
| 1 | SSH access to the VPS | 2 vCPU / 4 GB minimum, Ubuntu 22.04+ |
| 2 | Two DNS records they can create | `api.<domain>` and `app.<domain>` |
| 3 | Clerk account access | You need the JWKS URL, issuer, and publishable key |
| 4 | OpenRouter API key | Starts `sk-or-v1-…` |
| 5 | Vercel account access | Or tell you which host to use instead |
| 6 | Confirmation of who owns step 5 in "Where you stop" | The person who will connect accounts |

Do **not** generate the OpenRouter or Clerk keys yourself unless told to —
they're billed accounts and probably already exist.

---

## Step 1 — Point DNS at the VPS

Ask for an **A record** for `api.<domain>` pointing at the VPS's public IP.

This has to happen **before** step 4. Caddy asks Let's Encrypt for a
certificate on first start, and Let's Encrypt verifies by connecting to the
name. If it doesn't resolve to your VPS yet, issuance fails and you'll be
debugging TLS instead of the deployment.

Check it before continuing:

```bash
dig +short api.<domain>          # must print the VPS IP
```

If that prints nothing, DNS hasn't propagated. Wait — do not proceed and hope.

---

## Step 2 — Prepare the VPS

```bash
ssh <user>@<vps-ip>

# Docker, if it isn't there
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit                             # log out and back in so the group applies
```

```bash
ssh <user>@<vps-ip>
docker --version                 # should print a version
docker compose version           # should print v2.x — "compose", not "compose-plugin"
```

Only ports 80 and 443 need to be open. If there's a firewall:

```bash
sudo ufw allow 80,443/tcp
sudo ufw enable
```

Nothing else should be exposed. Postgres and Redis deliberately have no host
ports — an open 5432 on a public VPS gets found by scanners within hours.

---

## Step 3 — Configure

```bash
git clone <repo-url> b2b-pulse
cd b2b-pulse
git checkout claude/merge-social-bot-phase-1
cp .env.vps.example .env
```

Generate the three secrets. Run each and paste the output into `.env`:

```bash
# POSTGRES_PASSWORD
openssl rand -base64 32 | tr -d '/+='

# JWT_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# FERNET_KEY  — back this up somewhere off this server before continuing
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then edit `.env` and fill in the rest:

```bash
nano .env
```

| Setting | Value | Watch out for |
|---|---|---|
| `API_DOMAIN` | `api.<domain>` | No `https://`, no trailing slash |
| `CORS_ORIGINS` | `https://app.<domain>` | This is the **frontend's** address, not the API's. With `https://`. **No trailing slash** |
| `POSTGRES_PASSWORD` | generated above | |
| `JWT_SECRET` | generated above | |
| `FERNET_KEY` | generated above | Back it up first |
| `CLERK_JWKS_URL` | from Clerk | |
| `CLERK_ISSUER` | from Clerk | |
| `OPENROUTER_API_KEY` | from your manager | |

`CORS_ORIGINS` is the single most commonly-fumbled value. If it is wrong the
frontend loads perfectly and every single request fails, which looks like a
much more mysterious problem than it is. A trailing slash breaks it. `http`
instead of `https` breaks it.

Leave `CLERK_DEV_UNSAFE` and `ALLOW_UNCAPPED_SENDING` as `false`. The first
accepts unsigned login tokens; the second removes every rate cap if Redis has a
hiccup. The verification script fails if either is on.

---

## Step 4 — Start it

```bash
docker compose -f docker-compose.vps.yml up -d --build
```

First build takes 5–10 minutes.

Database migrations run as a **gated one-shot**: nothing else starts until they
succeed. Watch them:

```bash
docker compose -f docker-compose.vps.yml logs -f migrate
```

You want to see it run through revisions `001_initial` to `012_action_skipped`
and exit. If it fails, the API and workers deliberately won't start — a
half-migrated database is worse than an outage. Copy the error and ask.

Then check everything came up:

```bash
docker compose -f docker-compose.vps.yml ps
```

All of `postgres`, `redis`, `api`, `worker`, `beat`, `caddy` should say
`running`. `migrate` should say `exited (0)` — that's correct, it's a one-shot.

Caddy needs a minute or two to get its certificate. Then:

```bash
curl https://api.<domain>/health
# {"status":"healthy","app":"B2B Pulse","version":"0.1.0"}
```

---

## Step 5 — Verify

This is the part that tells you whether you're done. Don't skip it and don't
eyeball the logs instead.

```bash
./scripts/verify_deployment.sh https://api.<domain>
```

It checks configuration, containers, the database schema, the API, and — most
importantly — that the **rate limiter actually refuses** when a cap is reached,
by exercising it against your live Redis. That is the control that keeps
accounts inside LinkedIn's limits, so a deployment where it doesn't work is a
deployment that will get someone restricted.

Everything is read-only and nothing touches LinkedIn, so run it as often as you
like.

You are looking for **0 failed**. Warnings are usually fine (a missing
OpenRouter key, for instance). Failures are not — each one prints what to do.

---

## Step 6 — Deploy the frontend

On **Vercel**:

1. New Project → import the repo
2. **Root Directory: `frontend`** ← easy to miss, and nothing works without it
3. Framework preset: Vite (should autodetect)
4. Environment Variables:

   | Name | Value |
   |---|---|
   | `VITE_API_URL` | `https://api.<domain>/api` — note the `/api` on the end |
   | `VITE_CLERK_PUBLISHABLE_KEY` | `pk_live_…` from Clerk |

5. Deploy
6. Add the custom domain `app.<domain>` and create the DNS record Vercel asks
   for

Both variables are read **at build time** — Vite bakes them into the JavaScript
bundle. Changing them later requires a redeploy; setting them after the fact
does nothing.

`frontend/vercel.json` already handles the SPA routing and caching, so there's
nothing else to configure.

> Using Netlify or Cloudflare Pages instead? Base directory `frontend`, build
> `npm run build`, publish `dist`, same two environment variables.
> `frontend/public/_redirects` handles routing there. See
> [DEPLOYMENT.md](DEPLOYMENT.md) for serving it from the VPS instead.

---

## Step 7 — Point Clerk at it

In the Clerk dashboard, add `https://app.<domain>` to the allowed origins.
Until you do, login redirects will be rejected.

---

## Step 8 — Confirm it works end to end

1. Open `https://app.<domain>` — the app should load
2. Sign up / log in through Clerk — you should land on the dashboard
3. Open the browser console (F12) — **no red CORS errors**
4. Refresh the page while on a sub-page like `/console` — it should reload, not
   404

If step 3 shows CORS errors: `CORS_ORIGINS` on the VPS doesn't exactly match
the frontend's address. Fix `.env`, then:

```bash
docker compose -f docker-compose.vps.yml up -d api
```

If step 4 404s: the static host's SPA fallback isn't active. On Vercel, confirm
the root directory is `frontend` so `vercel.json` is being picked up.

---

## Where you stop

**You are done.** Do not continue past this line.

The next step is connecting a LinkedIn account, and that is not yours to do —
not with a real account, not with a test account, not with your own. Not
because you'd do it wrong, but because it is the one step in this process that
cannot be undone, and it belongs to whoever owns the accounts.

Hand back with this:

```
B2B Pulse deployment complete.

  API:      https://api.<domain>
  Frontend: https://app.<domain>

  verify_deployment.sh: <N> passed, 0 failed, <N> warnings
  Migrations: at 012_action_skipped
  Login through Clerk: confirmed working

  FERNET_KEY backed up to: <where>

No LinkedIn account has been connected. Ready for you to connect the
first one.

  Open items: <anything that warned, or anything you had to guess at>
```

If you had to guess at *anything*, say so in the open items. A guess you
mention costs five minutes; a guess you don't mention costs a LinkedIn account.

---

## When something goes wrong

Everything below is safe to run.

**Where do I look?**

```bash
docker compose -f docker-compose.vps.yml logs -f api      # the application
docker compose -f docker-compose.vps.yml logs -f worker   # the scheduled work
docker compose -f docker-compose.vps.yml logs -f caddy    # TLS and certificates
docker compose -f docker-compose.vps.yml logs migrate     # database schema
```

**`migrate` exited non-zero.** Read its log. Nothing else will start until it
succeeds, which is deliberate. Don't try to fix the database by hand — copy
the error and ask.

**Caddy can't get a certificate.** Almost always DNS (`dig +short api.<domain>`
doesn't return the VPS IP) or port 80 closed. Let's Encrypt validates over
HTTP, so 443 alone isn't enough.

**A container keeps restarting.** `docker compose -f docker-compose.vps.yml
logs <name>`. Usually a missing or malformed value in `.env`.

**The app loads but nothing works.** Browser console (F12). CORS errors mean
`CORS_ORIGINS`. 401s everywhere mean Clerk isn't configured on one side or the
other.

**I need to start completely fresh.**

```bash
docker compose -f docker-compose.vps.yml down -v    # -v deletes the database
docker compose -f docker-compose.vps.yml up -d --build
```

Safe right now because there's no real data yet. It will **not** be safe once
accounts are connected — `-v` destroys the encrypted credentials.

**I think I broke something.** Nothing here has touched LinkedIn, so nothing is
unrecoverable. Say what you ran and what it said.

---

## Things not to do

- **Don't connect a LinkedIn account.** The whole point of the stop.
- **Don't commit `.env`.** Check `git status` before any commit.
- **Don't run two `beat` containers.** Never `--scale beat=2`. Two schedulers
  make every account do its day twice — over its own safety caps, from our own
  software. `worker` can be scaled; `beat` cannot.
- **Don't set `ALLOW_UNCAPPED_SENDING=true`** to make something work. It
  removes every per-account rate cap.
- **Don't set `CLERK_DEV_UNSAFE=true`** on a public server. It accepts unsigned
  tokens, so anyone who finds the URL can log in as an admin.
- **Don't turn off Redis persistence.** The rate-limit counters live there.
  Wiping them hands every account a fresh daily and weekly allowance, which is
  exactly the thing the caps exist to prevent.
- **Don't expose Postgres or Redis on host ports** to "make debugging easier".
  Use `docker compose exec`.

---

## Reference

- [DEPLOYMENT.md](DEPLOYMENT.md) — fuller operator guide: backups, upgrades,
  the console stop button, and what to expect once accounts are connected
- `.env.vps.example` — every setting, with an explanation
- `docker-compose.vps.yml` — the stack, commented
