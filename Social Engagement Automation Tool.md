# PRD: AutoEngage – Social Engagement Automation Tool

## 1. Problem \& Goals

### 1.1 Problem

Your marketing \& sales teams spend stupid amounts of time manually liking and commenting on posts from key pages (clients, partners, internal brands) across LinkedIn and Instagram/Facebook. Doing this fast and consistently is critical for visibility and relationship-building, but it’s repetitive and easy to drop.

You also have a central WhatsApp group where people post links to new content, which still requires manual clicking, liking, and adding thoughtful comments.

### 1.2 Product Goal

Build a standalone web app (“AutoEngage”) that:

- Connects to users’ LinkedIn and Meta accounts (Instagram/Facebook).
- Lets them upload/define a list of target pages/profiles.
- Automatically detects new posts via:
    - WhatsApp group link drops, and
    - Cron‑based polling of those pages.
- Likes and comments on these posts in each user’s own style using LLMs, with brand-safe constraints.
- Provides analytics and a full audit trail.
- Is architected from day one as multi-tenant, Python backend, and easy to orchestrate with n8n.

***

## 2. Users \& Roles

### 2.1 User Types

- **Org Owner / Admin**
    - Creates org \& workspaces.
    - Configures integrations (LinkedIn, Meta, WhatsApp).
    - Manages member access, workspaces, and global settings.
    - Can see analytics for all members.
- **Member**
    - Connects their own LinkedIn / Meta accounts.
    - Configures their personal profile markdown, preferences, quiet hours.
    - Manages their own tracked pages and polling modes.
    - Views their own analytics and logs.
- **Analyst (v2, but model for it now)**
    - Read-only access to analytics for the org.
    - Cannot change automations or integrations.

***

## 3. Core Use Cases (MVP)

1. **Connect accounts**
    - User signs up, joins/creates org.
    - Connects:
        - LinkedIn personal profile via OAuth.
        - LinkedIn page via OAuth if admin.
        - Instagram Business / Creator account \& related Facebook page via Meta OAuth.[^1][^2]
    - Connects one WhatsApp account (via QR, WhatsApp Web automation instance) for group monitoring.[^3][^4]
2. **Define user profile \& brand rules**
    - Onboarding wizard:
        - Asks natural-language questions about tone, style, topics, emoji usage.
        - Generates a **user profile markdown** (stored per user) summarizing voice, content preferences, and do/don’t rules.
    - Allow upload of:
        - Brand book PDFs / docs.
        - Markdown/Word/CSV specifying phrases to avoid, brand taglines, etc.
    - System builds:
        - Style profile.
        - List of “AI tell” phrases to avoid, built from user’s input plus a default list maintained in DB that you update over time (e.g., “thanks for sharing,” “great insights,” “this is very insightful”).[^5][^6]
3. **Manage tracked pages**
    - User can:
        - Add pages via:
            - Direct input of URL(s) (LinkedIn profile/page, Instagram handle, Facebook page).
            - Bulk upload via CSV/Excel (list of URLs + tags).
        - Tag pages (e.g., “Tier 1,” “ICP,” “Client,” “Internal”).
    - System normalizes and stores:
        - Platform type.
        - Internal canonical ID for each page/profile.
    - Ability to toggle:
        - Auto-like and auto-comment per page.
        - Polling mode per page (normal vs hunting-first-comment).
4. **WhatsApp link triggers**
    - Connect one WhatsApp account via WhatsApp Web session, using QR-based login.
    - User registers one or more **WhatsApp groups** to monitor.
    - When any **message with a URL** appears:
        - Parse the URL to detect LinkedIn / Instagram / Facebook posts.
        - If the URL domain and path match a tracked page:
            - Immediately enqueue “engagement job” for all relevant user accounts that follow that page.
            - Mark that post as “seen” to avoid duplicate work.
        - If not a tracked page:
            - Suggest adding that page to tracked list (UI notification).
    - WhatsApp trigger **overrides** cron: if link is seen, cron won’t double-post, but can be used to backfill missed posts.
5. **Polling / cron-based detection**
    - Per-user, per-workspace polling job configuration:
        - Normal mode: every 5–10 minutes.
        - Hunting-first-comment mode: 30–60 seconds during user‑defined windows (e.g., 9–11 AM, 7–10 PM IST).
    - For each tracked page:
        - Fetch its latest posts via:
            - Official APIs where possible (page-owned content). For Instagram/Facebook, use Instagram Graph / Facebook APIs to read business/creator content.[^2][^7][^1]
            - Browser automation (Playwright) for personal profiles / unsupported endpoints.[^8][^9]
        - Compare against last-seen post ID.
        - If new post found:
            - Enqueue like/comment jobs for all relevant user accounts.
6. **Auto-like \& auto-comment**
    - For each new post needing engagement:
        - Determine which user accounts should act:
            - Use round-robin or simple queue across members that follow that page.
            - Option: All accounts like, comments staggered.
        - Risk profile:
            - **Safe mode**: random delays, smaller batch sizes, spaced actions.
            - **Aggro mode**: near‑instant actions, minimal delay.
        - Perform action via:
            - Official APIs for allowed scenarios (e.g., page commenting on its own posts where APIs permit).[^10][^11][^12]
            - Browser automation headless sessions for profile-level or unsupported interactions.[^9][^8]
        - Auto-throttle:
            - Keep per-account counters.
            - If actions fail with rate-limiting or errors characteristic of blocking:
                - Switch that account to “throttled mode” (reduced pace) automatically and alert user.
7. **Comment generation logic**
    - Inputs to LLM:
        - Post content (text, maybe alt text / captions).
        - User profile markdown.
        - Brand rules + avoid list (AI tells).
        - Page tags and relationship (client, partner, internal).
    - Flow:
        - Use Claude Sonnet to generate 1–3 variants of short, conversational, emoji‑friendly comments (1–3 short sentences, ideally one-liners).[^13]
        - Enforce max ~3 lines equivalent in token post-processing.
        - Use Claude Haiku for:
            - Style and compliance check against brand rules.
            - AI-tell phrase detection (using DB-based avoid list).
            - Optional rewrite if any banned phrases appear.
    - Requirements:
        - Use emojis frequently, but not in every sentence.
        - Avoid generic “thanks for sharing,” “great post,” etc.
        - Avoid sensitive topics (politics, religion), but allow news \& industry topics, with softening language.
    - Staggered comments:
        - If multiple team accounts target same post:
            - Likes can be immediate for all.
            - Comments are queued and spaced by random intervals within a user-defined window (e.g., 2–20 minutes).
            - Round-robin order ensures fairness across members.
8. **Review-before-post mode (optional per user/org)**
    - If enabled:
        - Comment is generated and placed in an “approval queue.”
        - User can choose how to receive approval requests:
            - Internal notification center.
            - Slack webhook (message containing post snippet + proposed comment + approve/reject buttons).
            - WhatsApp/Telegram via webhook (by hitting your own WhatsApp/Telegram integration endpoint).
        - On approve:
            - System posts comment.
        - On reject:
            - Option to regenerate comment or skip.
9. **Pause \& quiet hours**
    - Per user:
        - Toggle automation per platform (LinkedIn on/off, Meta on/off).
        - Toggle per page (per tracked account).
        - Define quiet hours where system will:
            - Queue engagements but delay execution.
            - Or skip comments while still allowing likes (configurable later).
10. **Analytics \& audit trail**
    - MVP metrics:
        - Per user:
            - Likes/comments per day per platform.
            - Average reaction time from post creation to comment.
        - Per tracked page:
            - Number of posts detected.
            - Engagement coverage: % posts liked/commented.
            - Average reaction time.
        - Per tag/campaign:
            - Aggregate of above, by tag.
    - Audit log:
        - For every action:
            - User, org, workspace.
            - Platform and target URL.
            - Action type (like/comment).
            - Comment text (if any).
            - Timestamp, status.
        - Exposed in UI and via CSV export.
    - Post performance (v1 hybrid):
        - Option A: For certain platforms (where possible), pull impressions/engagement via official APIs if permissions exist.[^2]
        - Option B: Allow manual weekly input of metrics (e.g., copy from LinkedIn analytics once a week) and store them for correlation.

***

## 4. Non-functional Requirements

- **Language \& runtime:** Python 3.11+
- **Backend framework:** FastAPI or Django REST Framework (recommend FastAPI for speed \& async; easier Playwright integration).
- **Database:** PostgreSQL (managed, e.g., Supabase/RDS).
- **File storage:**
    - Primary: Cloudflare R2 (S3-compatible).
    - Optional integration path to Google Drive later.
- **Infra:**
    - Containerized (Docker), deployable on Railway/Fly.io/Render.
    - Separate worker processes for:
        - Polling jobs.
        - WhatsApp listener.
        - Engagement executor.
- **Auth:**
    - App auth: Email/password + OAuth (Google) optional.
    - Social auth: LinkedIn OAuth, Meta OAuth.
- **n8n integration:**
    - Outbound: Webhooks for key events.
    - Inbound: Webhook endpoints that n8n flows can hit to enqueue custom jobs or override behavior.

***

## 5. Architecture Overview

### 5.1 High-level components

1. **Web frontend**
    - Simple React/Vue SPA or minimal template-based UI.
    - Key screens:
        - Onboarding wizard (user profile \& brand rules).
        - Integrations \& accounts.
        - Tracked pages management.
        - Automation settings (polling, risk profile, quiet hours).
        - Review queue (if enabled).
        - Analytics dashboard.
        - Audit log screen.
2. **API backend (FastAPI)**
    - Handles:
        - Auth \& multi-tenant org/workspace logic.
        - CRUD for user profiles, tracked pages, settings.
        - Integration token management.
        - Job scheduling triggers (enqueue into a queue).
        - Webhook endpoints (Slack, n8n, etc.).
3. **Worker \& scheduler**
    - Background workers (e.g., Celery, RQ, or a custom async job runner like APScheduler + Redis):
        - Polling worker:
            - Runs per-user, per-workspace jobs.
            - Calls platform APIs or automation scripts.
        - Engagement worker:
            - Generates comments (LLM calls).
            - Executes browser/API actions.
        - Notification worker:
            - Sends Slack/webhook/WhatsApp/Telegram approvals and notifications.
4. **Browser automation layer**
    - Python Playwright:
        - Maintains sessions per user/platform (LinkedIn / Instagram / Facebook).
        - Supports:
            - Login (initial manual login, then cookies stored encrypted).
            - Visiting profile/page URLs, scrolling until new posts found.
            - Clicking like and opening comment boxes to post comments.
        - Use stealth settings and human-like delays to avoid rapid detection.[^14][^8][^9]
5. **WhatsApp listener**
    - MVP: The robust ecosystem is Node + whatsapp-web.js.[^15][^16][^4]
    - This PRD’s constraint is “complete Python backend,” so options:
        - Option 1 (recommended): Small Node sidecar service running whatsapp-web.js, with a simple HTTP/WS interface for Python backend. Python stays core but this micro-service is highly pragmatic.[^17][^3]
        - Option 2 (pure Python): Use Playwright or Selenium to drive WhatsApp Web UI and scrape group messages. More janky and brittle, but consistent with “all Python.”
    - The listener:
        - Watches configured group(s).
        - Parses URLs.
        - Calls backend webhook `/events/whatsapp-link` with message metadata and URL.
6. **LLM integration**
    - Abstraction layer so models are swap‑able.
    - Models:
        - Free/cheap model (e.g., Gemini Flash) for any content classification or light parsing.
        - Claude Sonnet for comment generation.
        - Claude Haiku for review/compliance check.
    - Store model responses for audit/debug.

***

## 6. Data Model (MVP)

Key tables (simplified):

- `org`
    - id, name, created_at
- `workspace`
    - id, org_id, name
- `user`
    - id, org_id, email, role (owner/admin/member/analyst), encrypted password / SSO ID
- `user_profile_markdown`
    - user_id, markdown_text, created_at, updated_at
- `brand_asset`
    - id, org_id, workspace_id, file_url (R2), type (brand_book, avoid_list, etc.)
- `ai_avoid_phrase`
    - id, org_id (nullable for global), phrase, active
- `integration_account`
    - id, user_id, platform (linkedin, meta, whatsapp), access_token/encrypted, refresh_token, settings (risk_profile, quiet_hours, etc.)
- `tracked_page`
    - id, workspace_id, platform, external_id (canonical), url, name, type (personal, company, ig_business, fb_page), active
- `tracked_page_subscription`
    - id, tracked_page_id, user_id, auto_like (bool), auto_comment (bool), polling_mode (normal/hunt), tags (jsonb)
- `polling_job`
    - id, workspace_id, user_id, status, schedule_config (json)
- `post`
    - id, tracked_page_id, platform, external_post_id, url, created_at_platform, first_seen_at
- `engagement_action`
    - id, post_id, user_id, type (like/comment), status (pending/sent/failed), attempted_at, completed_at, error_message, comment_text
- `analytics_snapshot` (daily)
    - date, org_id, workspace_id, user_id, summary JSON (counts, reaction times)
- `webhook_subscription`
    - id, org_id, workspace_id, event_type, target_url, secret
- `review_queue_item`
    - id, engagement_action_id, status (pending/approved/rejected), approver_user_id, review_channel (slack/webhook/whatsapp)

***

## 7. Platform-specific notes \& constraints

### 7.1 LinkedIn

- Official API:
    - Community Management APIs can be used for page posts, comments, and reactions with proper permissions.[^11][^12][^10]
    - LinkedIn API ToS is cautious about automation and fake engagement, especially in 2025–26.[^18][^19][^5]
- Browser automation:
    - Use Playwright to act as a real user for:
        - Personal profile commenting.
        - Liking posts on feeds or profile pages.


### 7.2 Instagram/Facebook

- Official API:
    - Instagram Graph API supports reading media and posting comments on business/creator accounts connected to a Facebook page.[^20][^1][^2]
    - Additional permissions required for insights and advanced features.
- For non-owned content or personal accounts:
    - Use Playwright automation to like/comment on target posts.[^21][^22][^23]


### 7.3 WhatsApp

- Using WhatsApp Web behavior:
    - Interact with existing group chats using WhatsApp Web automation.
    - Unofficial methods carry risk of number blocks if abuse is detected, so safe-mode pacing is important.[^24][^25][^3]

***

## 8. Risk profile \& throttling logic

- **Safe Mode**
    - Limit likes/comments per account per day (configurable).
    - Insert human-like random delays (1–7 seconds) between browser actions.
    - Avoid burst actions at the exact same timestamp.
- **Aggro Mode**
    - Higher per-day limits.
    - Minimally spaced actions.
    - Recommended primarily for internal pages and controlled environments.
- **Auto-throttling**
    - Track errors from APIs and UI selectors (e.g., repetitive failures).
    - On suspicious pattern:
        - Downgrade to Safe Mode.
        - Slow interval for that account.
        - Show banner in UI for that user with recommended manual cool-down.

***

## 9. APIs \& Integration Points (MVP)

Backend should expose:

- `/auth/*` for signup/login/org creation.
- `/integrations/linkedin/connect`, `/integrations/meta/connect`, `/integrations/whatsapp/init`.
- `/user/profile`:
    - GET/PUT for markdown \& tone settings.
- `/brand/assets`:
    - Upload \& list brand docs.
- `/tracked-pages`:
    - CRUD, bulk upload.
- `/automation/settings`:
    - Risk profile, quiet hours, polling modes.
- `/review-queue`:
    - List pending items, approve/reject.
- `/webhooks/events`:
    - Internal endpoint for WhatsApp listener \& external automations (n8n).
- `/analytics/*`:
    - Summary metrics per org, workspace, user, page.

Outbound webhooks:

- `engagement.created`
- `engagement.completed`
- `engagement.failed`
- `review.requested`
- `review.approved`
- `review.rejected`

***

## 10. MVP Scope for “this weekend”

If you need something working by the weekend, a realistic MVP v0.1:

1. Core web app with:
    - Org + user auth.
    - Manual entry of LinkedIn URLs.
    - Single WhatsApp group listener (even if mocked or using a dev script).
2. Playwright-based LinkedIn automation:
    - Manual login step.
    - Ability to:
        - Go to a post URL.
        - Click like.
        - Add comment.
3. User profile markdown:
    - Simple onboarding form.
    - Store preferences.
4. Comment generation:
    - Simple call to Sonnet + Haiku with:
        - Post text pasted manually for first demo.
        - User profile used for style.
5. Simple cron:
    - 5–10 minute polling of a single LinkedIn profile feed.
6. Audit log:
    - Single table capturing actions.
    - Basic UI list.

Everything else (multi-tenant polish, Meta integration, full WhatsApp automation, Slack review flow, analytics dashboards) can layer on top of this core.

***
<span style="display:none">[^26][^27][^28][^29]</span>

<div align="center">⁂</div>

[^1]: https://developers.facebook.com/docs/instagram-platform/overview/

[^2]: https://elfsight.com/blog/instagram-graph-api-complete-developer-guide-for-2026/

[^3]: https://pinault.org/blog/whatsapp-web-js-a-comprehensive

[^4]: https://www.npmjs.com/package/whatsapp-web.js?activeTab=readme

[^5]: https://blog.closelyhq.com/linkedin-api-for-developers-what-you-can-and-cant-do/

[^6]: https://salesflow.io/blog/the-ultimate-guide-to-safe-linkedin-automation-in-2025

[^7]: https://stackoverflow.com/questions/57684067/required-graph-api-permissions-to-get-my-own-instagram-posts

[^8]: https://www.autopilotai.app/blog/how-to-build-a-browser-automation-script-for-linkedin-outreach-beginner-friendly

[^9]: https://www.linkedin.com/posts/sohailkhan2k22_python-playwright-webautomation-activity-7342239205663674368-Zxe9

[^10]: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api?view=li-lms-2026-01

[^11]: https://developer.linkedin.com/product-catalog/marketing/community-management-api

[^12]: https://stackoverflow.com/questions/71244083/linkedin-api-likes-shares-comments

[^13]: https://www.linkedin.com/pulse/engagement-playbook-comments-dms-tactics-nobody-talks-serge-bulaev-ylble

[^14]: https://github.com/ManiMozaffar/linkedIn-scraper

[^15]: https://wwebjs.dev

[^16]: https://docs.wwebjs.dev

[^17]: https://github.com/pedroslopez/whatsapp-web.js/

[^18]: https://www.linkedin.com/legal/l/api-terms-of-use

[^19]: https://www.linkedin.com/help/linkedin/answer/a519947/third-party-applications-data-use?lang=en

[^20]: https://elfsight.com/blog/instagram-graph-api-changes/

[^21]: https://www.youtube.com/watch?v=Q5kw7vGLqgs

[^22]: https://www.youtube.com/watch?v=j65W-L-gQs4

[^23]: https://github.com/imakashsahu/Instagram-Graph-API-Python

[^24]: https://erwinvanginkel.com/whatsapp-web-protocol-aka-unofficial-api/

[^25]: https://wasenderapi.com/blog/unofficial-whatsapp-api-a-complete-2025-guide-for-developers-and-businesses

[^26]: https://www.npmjs.com/package/whatsapp-web.js/v/1.15.8

[^27]: https://vault.nimc.gov.ng/blog/whatsapp-web-js-new-version-and-features-1764797643

[^28]: https://www.linkedin.com/posts/dhinesh-k2004_python-automation-playwright-activity-7394427370679521281-k0eM

[^29]: https://github.com/pedroslopez/whatsapp-web.js/releases

