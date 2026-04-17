# ChampMail Connector — Chrome Extension (dev)

Syncs the user's LinkedIn `li_at` session cookie to the ChampMail backend so
automation can act on their behalf, without requiring them to paste cookies
or share a password.

## How it is paired with the web app

1. The user signs in to the web app and lands on the LinkedIn connect page.
2. The page calls `POST /api/integrations/extension/pair` (JWT auth) and gets
   a short-lived pairing token.
3. The page invokes `chrome.runtime.sendMessage(EXT_ID, { type: 'PAIR',
   token, apiBase })`. This extension's background service worker receives
   the message, stores the token, reads `li_at` from `.linkedin.com`, and
   POSTs it to `/api/integrations/extension/session-cookies`.
4. The backend returns a long-lived `extension_token` (30-day rolling TTL).
   The extension uses this for every subsequent sync.

## Sideload (local dev)

1. `chrome://extensions` → enable **Developer mode** (top right).
2. **Load unpacked** → select this directory.
3. The extension ID is deterministic (the `key` in `manifest.json` pins it):
   `hmaaogphomlflfebfobfhfbnhaffllej`
4. Paste that ID into `frontend/.env` as `VITE_EXTENSION_ID=...` and rebuild
   the frontend container.
5. Pin the extension icon so the popup is visible.

## Keys

- `key.pem` is the private RSA key used to derive the extension ID. **Do not
  commit** in production — only the public `key` field in `manifest.json`
  is needed for Chrome to recognise the ID. For dev local-only use we commit
  `key.pem` next to the manifest so every developer gets the same extension
  ID; for production builds you'll regenerate and keep `key.pem` out of the
  repo.

## Permissions justification

- `cookies` + `host_permissions: *://*.linkedin.com/*` — read the user's own
  `li_at` session cookie to forward to our backend on their behalf.
- `storage` — remember the pairing / extension token and sync state.
- `alarms` — wake up every 4h to re-sync in case LinkedIn rotates the cookie.
- `notifications` — reserved for "reconnect needed" toasts (not used in MVP).
- `host_permissions: http://localhost:8001/*` — local dev backend. In the
  Web Store build this is replaced with the production API origin.

## Production hardening checklist

- Rotate `key.pem`; keep it out of version control.
- Swap `host_permissions` and `externally_connectable.matches` to the
  production domains.
- Add a privacy policy URL to the store listing.
- Submit for Chrome Web Store review ($5 one-time) + Edge Add-ons (free).
