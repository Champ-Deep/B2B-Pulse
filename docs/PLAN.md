# LinkedIn Dual-Path Onboarding — Implementation Plan

## Goal

Replace the current brittle three-way LinkedIn connection UX (cookie paste / Playwright-on-server / OAuth-that-doesn't-capture-cookies) with a production-friendly, dual-path flow:

1. **Primary (recommended):** a Chrome/Edge extension reads the user's own `li_at` cookie from their logged-in LinkedIn session and ships it to our backend.
2. **Fallback (always available):** the existing Playwright-assisted email/password login running on our backend, used when the user can't install an extension.

Both paths end at the same place — `IntegrationAccount.session_cookies` populated with a validated, encrypted cookie the worker can use.

A third path (manual `li_at` paste via DevTools) is retained behind an "Advanced" disclosure for power users.

## Why an extension

LinkedIn OAuth does not return automation-capable session cookies — the `access_token` only unlocks a narrow set of profile/share APIs. The only thing that lets the worker act *as* the user (like, comment, message) is the `li_at` cookie from a live browser session. The extension is the cleanest way to obtain and refresh that cookie because:

- Login happens in the user's own browser (on their home IP, with their own 2FA device).
- Cookie refresh is automatic — the extension re-reads the cookie on change and on a 4-hour alarm.
- No password ever touches our servers.
- LinkedIn bot-detection risk is minimal compared to Playwright on a datacenter IP.

Playwright stays as a fallback for users on locked-down Chrome profiles, non-Chrome browsers, or who refuse extensions.

## Architecture

```
┌──────────────────────┐                    ┌──────────────────────┐
│  User's Chrome       │                    │  ChampMail backend   │
│                      │                    │  (localhost:8001)    │
│  ┌────────────────┐  │                    │                      │
│  │ linkedin.com   │  │                    │  /integrations/      │
│  │  (logged in)   │  │                    │    extension/pair    │
│  └───────┬────────┘  │                    │    extension/cookies │
│          │ reads     │                    │    linkedin/         │
│          ▼ li_at     │  HTTPS + pairing   │      session-cookies │
│  ┌────────────────┐  │    token header    │      session-status  │
│  │   Extension    │──┼───────────────────►│      login-start     │
│  │   (MV3)        │  │                    │      login-verify    │
│  └────────┬───────┘  │                    │                      │
│           │          │                    │     Fernet encrypt   │
│  ┌────────▼───────┐  │                    │          ↓           │
│  │ app.localhost  │──┼────── JWT ────────►│   IntegrationAccount │
│  │   :5173        │  │                    │   .session_cookies   │
│  └────────────────┘  │                    │                      │
└──────────────────────┘                    │   celery-worker ─┐   │
                                            │                  ↓   │
                                            │      Playwright + cookie
                                            └──────────────────────┘
```

## Unified cookie schema

All three write-paths (extension, Playwright, paste) produce the same JSON shape in `IntegrationAccount.session_cookies` (encrypted with Fernet before storage):

```json
{
  "li_at": "...",
  "JSESSIONID": "..." ,          // optional, captured when available
  "captured_at": "2026-04-17T12:00:00Z",
  "source": "extension" | "playwright" | "paste"
}
```

- `session_expires_at` = `captured_at + 30 days` (LinkedIn's realistic `li_at` TTL, not the 365-day value currently written).
- `last_session_check` is bumped every time the watcher or worker successfully validates the cookie.
- The worker reads this blob with `_get_user_cookies()` in `backend/app/automation/linkedin_actions.py` without caring about `source`.

## User flow

**First-time signup**
1. User signs up at `/signup` or via "Sign in with LinkedIn" (OAuth, identity only).
2. Lands on `/onboarding/linkedin` with a three-choice picker:
   - **Extension (recommended, top card)** → "Add to Chrome" → install → pairing handshake → cookie synced → green ✓.
   - **Email & password (secondary card)** → existing Playwright-assisted flow with 2FA.
   - **Advanced: paste `li_at`** (collapsed) → existing paste form.
3. Once any path succeeds, advance to Tone setup, then TrackedPages, then done.

**Day N — cookie still fresh**
- Extension re-syncs every 4h + on cookie change. User sees nothing.
- Playwright/paste users rely on the 30-day TTL.

**Day N — cookie expired**
- Session watcher celery task (every 6h) detects invalid cookie → sets `settings.needs_reconnect = true` and emails the user.
- App shows a reconnect banner → one click → lands back on the same three-choice picker.
- Extension users typically reconnect just by logging back into LinkedIn (no app interaction).

## Phases

### Phase 1 — Backend prep

Estimated: 4–5h.

**File changes:**

- **`docker-compose.yml`** — fix `VITE_API_URL` port (currently `:8000`, backend is on `:8001`).
- **`.env`** — `CORS_ORIGINS=http://localhost:5173,chrome-extension://*`.
- **`backend/app/main.py`** — switch `CORSMiddleware` from `allow_origins=list` to `allow_origin_regex=r"^(https?://localhost:\d+|chrome-extension://.*)$"`; register new `extension` router.
- **`backend/app/api/integrations.py`**
  - Standardize the cookie dict shape produced by `/linkedin/session-cookies` and Playwright login paths (`_save_login_cookies`) to the unified schema above.
  - Change `session_expires_at` from `now + 365d` to `now + 30d`.
  - Improve Playwright rate-limit error payload so the UI can surface retry time.
- **`backend/app/api/extension.py` (new)** — two endpoints:
  - `POST /integrations/extension/pair` (JWT auth) → generates random token, stores `pair:<token> → user_id` in Redis with 10-min TTL, returns `{pairing_token, expires_at}`.
  - `POST /integrations/extension/session-cookies` (auth: `X-Pairing-Token` header) → reuses the same validate-encrypt-store logic as `/linkedin/session-cookies`, writes `source: "extension"`.
- **`backend/app/workers/tasks/session_watcher.py` (new, registered in `celery_app.py`)** — every 6h, for each active `LINKEDIN` `IntegrationAccount` with `is_active=true`, call `check_session_valid()`; on failure set `settings.needs_reconnect=true` and log (email send stubbed initially).
- **`backend/app/config.py`** — externalize `LINKEDIN_REDIRECT_URI` and `LINKEDIN_AUTH_REDIRECT_URI` so prod can override (localhost default stays as dev convenience).

**Acceptance:**
- `curl -X POST localhost:8001/api/integrations/extension/pair -H "Authorization: Bearer <JWT>"` returns a token.
- `curl -X POST localhost:8001/api/integrations/extension/session-cookies -H "X-Pairing-Token: <token>" -d '{"li_at":"..."}'` writes a row with `source: "extension"`.
- Old `/linkedin/session-cookies` still works (Advanced path).
- Playwright login flow still works end-to-end.

### Phase 2 — Chrome extension MVP

Estimated: 1–2 days.

**New directory:** `linkedin-extension/`

```
linkedin-extension/
├── manifest.json
├── background.js
├── popup.html
├── popup.js
├── icons/
│   ├── 16.png
│   ├── 48.png
│   └── 128.png
└── README.md
```

**`manifest.json`** — MV3, `"name": "ChampMail Connector (dev)"`, version `0.1.0`, `"key"` set for deterministic ID on reload.
- `permissions`: `cookies`, `storage`, `alarms`.
- `host_permissions`: `*://*.linkedin.com/*`, `http://localhost:8001/*`.
- `externally_connectable.matches`: `http://localhost:5173/*` (and the future prod domain).
- `background.service_worker`: `background.js`.
- `action.default_popup`: `popup.html`.

**`background.js` behavior**
- `chrome.runtime.onMessageExternal` listener accepts `{type: "PAIR", token, apiBase}` from the frontend, stores in `chrome.storage.local`, then calls `syncCookies()` immediately.
- `chrome.alarms.create("sync", {periodInMinutes: 240})` → `onAlarm` calls `syncCookies()`.
- `chrome.cookies.onChanged` filtered to `domain: ".linkedin.com", name: "li_at"` → debounced `syncCookies()` (max once/minute).
- `syncCookies()`:
  - `chrome.cookies.get({url: "https://www.linkedin.com", name: "li_at"})`.
  - If absent: update popup badge to "✕", POST nothing.
  - If present: `fetch(apiBase + "/api/integrations/extension/session-cookies", { method: "POST", headers: { "X-Pairing-Token": token, "Content-Type": "application/json" }, body: JSON.stringify({ li_at: cookie.value }) })`.
  - On 200: badge "✓", store `lastSyncedAt`.
  - On 401: clear token, badge "!", instruct popup to show "Reconnect from app".

**`popup.html` + `popup.js`** — tiny one-screen UI: status pill (connected/disconnected/error), last-sync timestamp, user name (if returned from backend), "Sync now" button, "Open dashboard" button.

**Acceptance:**
- Extension loads unpacked without errors.
- After frontend sends `PAIR` message, backend receives cookie and creates DB row.
- Deleting `li_at` in Chrome and restoring it triggers a re-sync within ~1 minute.

### Phase 3 — Frontend wiring

Estimated: 6–8h.

**File changes:**

- **`frontend/.env`** — `VITE_EXTENSION_ID=<deterministic id from manifest key>`.
- **`frontend/src/lib/extensionBridge.ts` (new)** — exports `isExtensionAvailable()` (feature-detects `chrome.runtime`), `pairExtension(token, apiBase)` (wraps `chrome.runtime.sendMessage`).
- **`frontend/src/pages/OnboardingLinkedIn.tsx` (new)** — the three-choice picker:
  - Card 1 (top): "Install ChampMail Connector" + "Add to Chrome" button. After install detection, shows "Connect LinkedIn" → calls `/extension/pair` → sends message to extension → polls `/linkedin/session-status` every 2s → green ✓ → "Continue".
  - Card 2: email/password form → existing `/linkedin/login-start` → `/linkedin/login-verify` (2FA code UI inline).
  - Advanced collapse: paste `li_at` → existing `/linkedin/session-cookies`.
- **`frontend/src/pages/Onboarding.tsx`** — insert navigation step to `/onboarding/linkedin` before TrackedPages.
- **`frontend/src/pages/AutomationSettings.tsx`** — top "Connection" card with current status (source, last check, expiry) + "Reconnect" button that navigates to the picker.
- **`frontend/src/components/layout/*`** — reconnect banner shown globally when `settings.needs_reconnect=true` (read from `/users/me` or `/linkedin/session-status`).
- **`frontend/src/lib/routes.ts`** — add `ONBOARDING_LINKEDIN: '/onboarding/linkedin'`.

**Acceptance:**
- Onboarding shows all three options; each successfully writes a row.
- Reconnect banner appears when watcher flips `needs_reconnect`.
- Settings shows which source is active.

### Phase 4 — Local end-to-end test

Estimated: 2h.

1. `docker compose up -d --build` — all 7 services healthy.
2. Sideload extension: `chrome://extensions` → Developer mode → Load unpacked → select `linkedin-extension/`.
3. **Test 4A (extension path):** sign up → onboarding → install prompt → click "Connect" → verify green ✓ within 5s → check DB for `source: "extension"` row.
4. **Test 4B (Playwright path):** fresh user → onboarding → email/password → handle 2FA → verify row with `source: "playwright"`.
5. **Test 4C (worker):** add a tracked post → trigger job → verify worker reads cookie and posts comment, regardless of source.
6. **Test 4D (expiry):** corrupt cookie in DB → run watcher task → banner appears → reconnect flow works end-to-end.

### Phase 5 — Packaging (optional, post-MVP)

- Zip `linkedin-extension/` for teammates to sideload.
- DUNS application for org-level Chrome Web Store publishing (~30 days lead time).
- Edge Add-ons submission (free, ~7 days review).
- Chrome Web Store submission once DUNS arrives (plan for 1–3 week review + likely revision cycle).

## Risks and mitigations

- **Chrome Web Store rejection** — extensions that read auth cookies + exfiltrate to a backend face elevated scrutiny. Mitigations: narrowest permissions, clear privacy policy, reviewer demo video, keep Playwright + paste paths so the app is still usable if review takes weeks.
- **LinkedIn detection of Playwright on our IP** — keep rate-limit, plan residential proxy via `BROWSER_PROXY_URL` for production.
- **LinkedIn TOS** — automation of commenting is against LinkedIn's TOS regardless of how cookies were obtained. Individual user accounts carry the risk. Document in user-facing terms.
- **Cookie encryption key rotation** — single `FERNET_KEY` today. Plan MultiFernet rotation before scale.

## Out of scope for this plan

- OAuth redesign beyond externalizing redirect URIs.
- Chrome Web Store submission (Phase 5 only).
- Edge/Firefox builds (same zip works, but branding + listing are separate work).
- Multi-account LinkedIn (one `IntegrationAccount` per user today).
