# Canvas Integration — Design (2026-07-20)

**Source:** `new-features.md` #10 — "be able to grab assignments and other
information from my U of U Canvas account, then add it to the todo, and calendar."

## Goal

Pull assignments, due dates, and course announcements from the user's University
of Utah Canvas account (`utah.instructure.com`) into Lumen:

- **Assignments** → auto-created **rich local todos** + **thin calendar due-date
  markers** (one-tap batch confirm).
- **Announcements** → a readable/queryable **feed** (chat + morning briefing),
  with a **light actionable-flag** that offers to turn a deadline-ish
  announcement into a todo (user-confirmed, never automatic).

Read-only *from* Canvas. Every external write (calendar) stays confirmation-gated.

## Feasibility — proven on the real account (spikes, 2026-07-20)

A two-round embedded-webview spike against the user's live account confirmed the
whole chain (results archived in scratch; not committed):

- A logged-in **browser session authorizes the Canvas REST API**
  (`/api/v1/users/self` returns JSON in the browser).
- **Python (`httpx`) can reuse that session**: cookies — including the httpOnly
  `canvas_session` — extracted from a QtWebEngine login window authenticated a
  plain Python API call → `200`, correct identity ("Joshua Sizemore", id 3087749).
- Real data pulled: **22 courses**, **35 announcements** (title / date / body),
  assignments carrying **due dates, points, and links**.
- `planner/items` returned **0 upcoming** — correct, not a bug: mid-summer, all
  assignments are from the concluded Spring 2026 term. Populates automatically at
  term start.
- **Credential storage** verified: GNOME Keyring Secret Service is running on the
  user's Hyprland session; Python `keyring` store→retrieve→delete round-trips.

This retires the two original blockers ("needs Duo login" / "webscraping"):
**no scraping** (clean JSON API), **no admin token** (session reuse), **no Duo
automation** (the user does Duo in a real login window).

## Non-negotiables it must respect

From `CLAUDE.md` / `project-scope.md`:

- Bulk sync is a plain background job, **never routed through the LLM / MCP loop**.
- **No silent external writes** — calendar events remain confirmation-gated.
  (Local todos are not an external write and are not gated.)
- **Read-only from Canvas** — Lumen never writes to Canvas.
- Power/thermal discipline — infrequent poll, no tight loops, model idle-unload
  untouched.
- Secrets never hardcoded or written to `.env`/config; inspectable where possible.

## Architecture & the daemon/UI split

The login window is inherently GUI (QtWebEngine) → it lives in the **UI**
(`ui_v3`). Sync, mirror, and reconciliation are backend → they live in the
**daemon**. The seam: the UI owns login + session and hands the **session cookies**
to the daemon over the existing unix-socket IPC. **The daemon never sees the
password** — only cookies.

### UI (`ui_v3`)

- `canvas_login.py` — QtWebEngine login window with a **persistent profile**
  (stay-logged-in across restarts). Best-effort autofill of uNID + password from
  the OS keyring. On successful login, extracts session cookies
  (`cookieStore().cookieAdded`) and pushes them to the daemon via IPC. Re-opens on
  true session expiry.
- `screens/canvas.py` — Canvas screen: assignments list + announcements feed
  (reads the mirror through the daemon). Relay-styled; visual layout deferred to
  the UI pass (`ui-spec.md`).
- Settings → Canvas section — Connect / Disconnect (+ Forget) / Save-login /
  sync status.

### Daemon

- `connectors/canvas_client.py` — `CanvasClient`: thin `httpx` wrapper over the
  Canvas REST API given the current session cookies. Read-only. Endpoints: active
  courses, per-course assignments (+ submission status), per-course announcements
  (`discussion_topics?only_announcements=true`), `planner/items`. Handles
  pagination, the `while(1);` JSON prefix, and maps `401` → "session dead".
- `connectors/canvas_store.py` — `CanvasStore`: SQLite mirror + reconciliation
  queries. Same shape as `email_menu.py`'s `EmailStore`.
- `connectors/canvas_sync.py` — background poller (default ~45 min), outside the
  LLM loop; pulls active courses into the mirror, then reconciles into todos /
  calendar / announcement-flags. Matches the email bulk-sync worker pattern.
- `mcp_servers/canvas.py` — read-only MCP tools over the mirror
  (`list_assignments`, `get_assignment`, `list_announcements`) so chat and the
  briefing can answer "what's due?" / "any new announcements?". Mirrors `mail.py`
  (read-only SQLite URI handle, zero API quota).

### Credential / session storage

- **Canvas uNID + password** → OS keyring (`keyring` → Secret Service), read only
  by the UI for autofill. Never in a file, never in `.env`, never in the daemon.
- **Session cookies** → held by the daemon in memory for the sync loop; the
  persistent QtWebEngine profile in the UI is the durable session home across
  restarts. If the daemon restarts, the UI re-sends the current cookies on
  reconnect (re-extracted from the still-logged-in profile), so a daemon bounce
  needs no new login.

## Auth & session flow

1. **Connect** — UI opens the login window → user logs in + Duo (autofill fills
   uNID/pw if saved) → QtWebEngine persistent profile holds the session → UI
   extracts cookies → sends them to the daemon over IPC.
2. **Sync** uses those cookies. On `401`, the daemon flags session-dead → UI
   silently reloads Canvas in the hidden webview to refresh the session; if still
   dead, it re-opens the visible login window.
3. **Disconnect** — clears the daemon session, wipes the QtWebEngine profile, and
   (Forget) deletes the keyring credentials.

## Sync & mirror

- Poll **active-enrollment courses only** (not the 22 historical). Cadence
  configurable via `[canvas] poll_minutes` (default 45). No tight loop; unaffected
  by model idle-unload.
- Per sync: for each active course, pull assignments (with submission status) and
  announcements; upsert into the mirror; record a per-course last-sync cursor.
- **Mirror schema** (in the existing SQLite db):
  - `canvas_courses(id, name, course_code, term, active, last_synced)`
  - `canvas_assignments(id, course_id, name, due_at, points, html_url,
    description, submitted, todo_id, calendar_event_id, first_seen, handled)`
  - `canvas_announcements(id, course_id, title, posted_at, message, html_url,
    seen, actionable, suggested_todo, todo_id)`
- Null course names are handled gracefully (fall back to `course_code`; the spike
  saw one such course).

## Assignments → todos + calendar

- A new assignment that is **not already submitted** → auto-create a **rich todo**
  via `TodoStore` (`source="canvas"`): text `"COURSE — Assignment name"`,
  `due_date` from `due_at` (converted to local tz), tags `[course, "canvas"]`;
  points, link, and description carried in the mirror row. Already-submitted →
  skip (do not nag).
- Their due dates **batch into a single confirm** — "Add these N due-dates to your
  calendar?" — through the existing calendar confirm-gate (`ConfirmBroker` /
  `create_event` gated path) → thin **all-day "X due" markers**. Nothing reaches
  Google Calendar without that one tap. Store `calendar_event_id` for
  reconciliation.
- **Reconciliation each sync:**
  - Due-date change → update the todo's due date and the calendar marker.
  - Assignment newly submitted → mark the linked todo done.
  - User deleted the todo (`todo_id` gone) → set `handled` and **do not recreate**.
  - Dedup strictly by Canvas assignment id.

## Announcements

- **Feed** in the Canvas screen (newest first, grouped by course); **queryable in
  chat** via the MCP tool; **folded into the morning briefing** (daily-features).
- On each **new** announcement only (one cheap call, not repeated), a **light
  classification** on the small resident model flags actionable / deadline-ish
  ones and extracts a suggested task + date → surfaces an **"Add as todo?"** offer
  (confirmed; never automatic). Non-actionable announcements just sit in the feed.
- A `seen` marker distinguishes new-since-last-sync.

## Config & dependencies

- `[canvas]` in `config.toml`: `enabled`, `poll_minutes` (default 45),
  `base_url` (default `https://utah.instructure.com`).
- No secrets in config/`.env`: password in keyring, session cookies in
  memory/profile.
- **New dependencies** (added to `pyproject.toml`): `PyQt6-WebEngine` (login
  window — already installed & import-verified), `keyring` (credentials — already
  installed & round-trip-verified).

## Safety & principles alignment

- Read-only from Canvas; the only writes are **local todos** (ungated) and
  **Google Calendar** (gated, batch-confirmed) — consistent with the existing
  archive/delete/event-create rules.
- Bulk sync is a plain daemon job; MCP exposes **read-only** tools over the
  mirror — matches the mail pattern and the "no bulk through the LLM" rule.
- Credentials via the **OS keyring** (browser-grade, encrypted at rest, unlocked
  by desktop login); the password never leaves the UI and never lands in a file.

## Known risks & fallbacks

1. **Session-cookie → API is undocumented Canvas behavior.** Works today; if
   Canvas ever blocks it, fall back to the **iCal calendar feed** (due dates only)
   or an **admin-issued personal access token** (same `CanvasClient`, swap the
   auth to a Bearer header).
2. **Persistence + autofill are best-effort.** Qt profile persistence and U of U
   login-form selectors can drift → degrade to more-frequent manual login, never a
   hard break.
3. **QtWebEngine is a heavy dep** (bundled Chromium) but only runs during the
   occasional login window — zero idle cost, acceptable on the iGPU laptop.
4. **Keyring collection may be locked** in some sessions → one unlock prompt
   (same as a browser). Verified unlocked on the user's current session.

## Out of scope (this feature)

- Writing to Canvas (submitting work, posting) — never.
- Historical/concluded courses in the live sync.
- Full gradebook / grades dashboard (assignments + announcements only for v1;
  grades are an API-supported later add).
- A general in-app Canvas browser (the login window is focused, not a browsing UI).
- SMS / other integrations (`new-features.md` #9).

## Build sequence (high level; detailed plan follows via writing-plans)

1. ✅ `CanvasClient` + `CanvasStore` + schema (daemon; unit-testable against recorded
   JSON captured in the spike).
2. ✅ Login window + cookie-handoff IPC + keyring credentials/autofill (UI).
3. ✅ Sync poller + reconciliation into todos + batch calendar confirm.
   (Detailed plan: `docs/superpowers/plans/2026-07-21-canvas-parts-4-5.md`.)
4. ✅ MCP read tools (`lumen/mcp_servers/canvas.py`) + briefing/chat wiring.
5. ✅ Announcements feed + actionable-flag (bounded small-model classify per new
   announcement; "Add as todo?" offer, user-confirmed).
6. ✅ Canvas screen content (assignments + announcements + confirm buttons).
   Settings section pending polish; visual layout deferred to `ui-spec.md`.
7. ⬜ Live verification on the real account (a current course, or fall-term data) —
   owed by Josh (QtWebEngine + Duo login can only be driven on the real machine).

**v1 note:** a due-date change re-lists the assignment in `pending_calendar` as an
`update`, so its calendar marker moves only after the user re-taps confirm —
deliberate, to honour "no silent external writes". The local todo's due date
updates automatically (not an external write).
