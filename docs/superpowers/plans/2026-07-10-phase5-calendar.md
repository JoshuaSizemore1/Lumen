# Phase 5 — Calendar (read, then write) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> *Authoring note:* this plan is being executed inline in the session that wrote it, so tasks carry exact interfaces, test intent, and commands rather than duplicating every implementation line — the code lands once, test-first, in each task's commit.

**Goal:** Real Google Calendar on the dashboard, month grid, and chat (from a locally synced cache), then confirmation-gated NL event creation through a generic confirm-over-IPC flow.

**Architecture:** `google_auth.py` (shared OAuth, Phase 6 reuses) + `EventStore`/`CalendarSync` in `connectors/gcal.py` polled by a plain asyncio task — never the LLM loop. A custom in-repo `mcp_servers/gcal.py` (FastMCP) exposes `list_events` now, `create_event` in the write half, sharing the same token. Router gains `calendar.list`/`calendar.create` one-shots, `CAL_HINT` context injection, and a create-intent pipeline (`daemon/llm/event_create.py`, book_recs pattern: extract → mechanical validation → confirm broker → tool call). `daemon/confirm.py` is the generic pause-for-confirmation broker Phase 4.5/6/7 reuse.

**Tech Stack:** Python 3.12, asyncio, stdlib sqlite3, PyQt6, google-api-python-client + google-auth-oauthlib (new), python-dateutil (new, RRULE validation), FastMCP (existing dep).

**Spec:** `docs/superpowers/specs/2026-07-10-phase5-calendar-design.md`

## Global Constraints

- All LLM calls via `daemon/llm/`; UI holds zero business logic; poller never touches LLM/MCP.
- `calendar_poll_minutes < 5` rejected at config load (no-tight-loops); idle-unload untouched.
- No calendar write executes without an explicit confirm over IPC; timeout (120 s) and disconnect deny.
- Timezones: store what the API returns (offset datetimes; bare dates + `all_day`), render local, compute "today"/free-busy with aware datetimes in the local zone.
- Scopes staged: `calendar.readonly` for the read half; `calendar.events` added only in Task 14.
- Async tests bare `async def`; Qt tests `qtbot` (offscreen set in conftest). `uv run pytest` must stay green at every commit.
- Commits: imperative summary, no credit trailers, end with `This commit used N prompts.` (N = prompts since last push; 11 at plan time — recompute if new prompts arrive).

---

### Task 1: Config — `[google]` and `[sync]` sections
**Files:** modify `lumen/daemon/config.py`; test `tests/daemon/test_config.py`.
**Produces:** `GoogleConfig(client_secret_path: Path, token_path: Path)` (defaults `$XDG_DATA_HOME/lumen/google/client_secret.json` / `token.json`), `SyncConfig(calendar_poll_minutes=5, calendar_window_past_days=30, calendar_window_future_days=60)`; `Config.google`, `Config.sync`. Loader reads `[google]`/`[sync]`, expanduser on paths, `SystemExit` if `calendar_poll_minutes < 5` ("no-tight-loops") or window days negative.
**Tests:** defaults when sections absent; TOML overrides land; poll-minutes 4 → SystemExit; past_days -1 → SystemExit.
- [ ] failing tests → run → implement → green → commit "Add google and sync config sections"

### Task 2: Schema — `events` + `sync_state`
**Files:** modify `lumen/daemon/db.py`; test `tests/daemon/test_db.py`.
**Produces:** tables per spec — `events` PK `(calendar_id, id)`, `attendees DEFAULT '[]'`, `all_day DEFAULT 0`; `sync_state(key PK, value)`.
**Tests:** insert/read row with defaults; PK uniqueness on (calendar_id,id) allows same id across calendars.
- [ ] failing tests → implement → green → commit "Add events and sync_state tables"

### Task 3: `google_auth.py` + deps + console script
**Files:** create `lumen/daemon/connectors/google_auth.py`; modify `pyproject.toml` (deps + `lumen-google-auth` script); test `tests/daemon/connectors/test_google_auth.py`.
**Produces:** `READ_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)`, `WRITE_SCOPES = READ_SCOPES + ("https://www.googleapis.com/auth/calendar.events",)`; `load_credentials(google_cfg, scopes) -> Credentials | None` (None if token file missing/unparseable or lacks scopes; refreshes+rewrites when expired w/ refresh token); `connected(google_cfg) -> bool`; `run_consent_flow(google_cfg, scopes)` (InstalledAppFlow, writes token 0600) behind `main()` for the script. No network in tests: fabricate token JSON files; monkeypatch refresh.
**Tests:** missing file → None/False; valid file → Credentials with scopes; scope-missing token → None; token file written 0600 by save helper.
- [ ] `uv add google-api-python-client google-auth-oauthlib python-dateutil` → failing tests → implement → green → commit "Add shared Google OAuth module and auth script"

### Task 4: `EventStore`
**Files:** rewrite `lumen/daemon/connectors/gcal.py` (stub); test `tests/daemon/connectors/test_gcal.py`.
**Produces:** `EventStore(conn)`: `replace_window(events: list[dict], start_iso, end_iso) -> None` (one transaction: DELETE window rows, INSERT set; window compare on `date(start_at)`), `list_range(start_iso, end_iso) -> list[dict]` (ordered all-day-first then start_at; attendees JSON-decoded, `all_day` bool), `set_last_sync(ts_iso)` / `last_sync() -> str | None` via `sync_state`. Event dict keys mirror columns.
**Tests:** replace clears only in-window rows; range query inclusive bounds incl. all-day date rows; ordering; last_sync roundtrip; attendees decode.
- [ ] failing tests → implement → green → commit "Add EventStore: cached calendar window queries"

### Task 5: `CalendarSync` + poll task + daemon wiring
**Files:** modify `lumen/daemon/connectors/gcal.py`, `lumen/daemon/__main__.py`; test `tests/daemon/connectors/test_gcal.py`.
**Produces:** `CalendarSync(store, google_cfg, sync_cfg, *, service_factory=None)`: `connected` property; `async sync_once() -> bool` (False when not connected/HTTP error — keeps stale cache; True on success: calendarList (skip `hidden`/`deleted`), per-calendar `events.list(singleEvents=True, timeMin, timeMax, pageToken loop)`, normalize (all-day `start.date` vs `start.dateTime`, attendees `[{email,name,self}]`, calendar colorId→hex via `colors` map fallback `backgroundColor`), `replace_window` + `set_last_sync`; runs API calls in `asyncio.to_thread`). `async poll_forever(stop_event)`: loop `sync_once` + `sleep(poll_minutes*60)`, exceptions logged not raised. `__main__` starts/cancels the task.
**Tests:** fake service objects (no network): multi-calendar merge, hidden skipped, all-day + timed normalization, pagination, not-connected → False and store untouched, HTTP error → False keeps cache + last_sync unchanged.
- [ ] failing tests → implement → green → commit "Sync Google Calendar into the local window cache on a 5-min poll"

### Task 6: Router — `calendar.list` + `CAL_HINT` context
**Files:** modify `lumen/daemon/router.py`, `lumen/daemon/__main__.py`; test `tests/daemon/test_router.py`.
**Produces:** module fn `calendar_context(events, now: datetime, window_end: date) -> str` (Now line w/ weekday+tz, bounds statement "…say so if asked", per-event compact line, explicit empty marker); `CAL_HINT` regex (calendar|meeting|event|schedule|agenda|free|busy|appointment); Router param `calendar=None` (an `EventStore`+sync facade: needs `list_range`, `last_sync`, `connected`); one-shot `calendar.list {from,to}` → `{"result": {"events", "connected", "last_sync"}}`; `_base_messages` injects calendar context for `CAL_HINT` (today → +14 d from cache).
**Tests:** hint vocab (+ negative); context injected w/ bounds+empty marker; one-shot payload passthrough + not-connected shape; hint precedence unchanged for REC/TOOL paths.
- [ ] failing tests → implement → green → commit "Serve calendar reads from the cache: one-shot and chat context"

### Task 7: MCP server — `lumen/mcp_servers/gcal.py` (read tool)
**Files:** create `lumen/mcp_servers/gcal.py`; test `tests/mcp_servers/test_gcal.py`; edit local `lumen/config.toml` (gitignored) to register server with `tools=["list_events"]`.
**Produces:** FastMCP server "gcal": `list_events(start: str, end: str) -> str` — ISO date args, real API call via `google_auth.load_credentials(READ_SCOPES)`, graceful strings on not-connected/HTTP failure (openlibrary convention), compact line-per-event output.
**Tests:** date validation message, not-connected message, formatting from a faked service.
- [ ] failing tests → implement → green → commit "Add gcal MCP server: live list_events for beyond-cache queries"

### Task 8: Dashboard live
**Files:** rewrite `lumen/ui/dashboard.py`; modify `lumen/ui/__main__.py`; create `tests/ui/test_dashboard.py`; adjust `tests/ui/test_screens.py`.
**Produces:** `DashboardScreen(client)`: on show → `todos.list` + `calendar.list {from:today,to:today}`; todos column live (open items, done styling); calendar column time-positioned blocks (range auto-widens past 08–20 when events fall outside), all-day pinned top, now-line, "synced Nm ago" from last_sync, "Google Calendar not connected — see docs/google-oauth-setup.md" state, offline banner on client error; mail column labeled "placeholder — live in Phase 6".
**Tests:** loads both one-shots on show; renders event titles/times; not-connected state; offline banner; all-day pinned; mail placeholder label present.
- [ ] failing tests → implement → green → commit "Wire the dashboard live: today's todos and real calendar"

### Task 9: Calendar screen live
**Files:** rewrite `lumen/ui/calendar_view.py`; modify `lumen/ui/__main__.py`; create `tests/ui/test_calendar_view.py`; adjust `tests/ui/test_screens.py`.
**Produces:** `CalendarScreen(client)`: month state (default today's month), ‹/›/Today nav re-request `calendar.list` for the visible grid range (Mon-start 6-week grid); real month math; today highlight; ≤3 chips/cell + "+N more"; legend = distinct calendar_name/color pairs from data; days outside sync window dimmed + one-line note when visible month exits the window; Week/Day buttons removed; "+ Event" button present (inert until Task 15); not-connected + offline states as dashboard.
**Tests:** grid month math (incl. month starting Monday and spanning 6 weeks), nav requests new range, chips + overflow, legend from data, outside-window dimming, not-connected state.
- [ ] failing tests → implement → green → commit "Wire the month calendar live from the cached window"

### Task 10: Setup walkthrough doc
**Files:** create `docs/google-oauth-setup.md`.
**Produces:** step-by-step: GCP project → enable Calendar API → OAuth consent (External, test user = self) → Desktop-app client → download JSON to `~/.local/share/lumen/google/client_secret.json` → `uv run lumen-google-auth` → verify dashboard. Notes: token 0600, re-consent when write scope arrives, revocation path.
- [ ] write doc → commit "Document the one-time Google OAuth setup"

### Task 11: Confirm broker + IPC routing
**Files:** create `lumen/daemon/confirm.py`; modify `lumen/daemon/ipc_server.py`, `lumen/daemon/router.py`; test `tests/daemon/test_confirm.py`, extend `tests/daemon/test_ipc_server.py`.
**Produces:** `ConfirmBroker(timeout=120)`: `async ask(payload: dict) -> tuple[int, "asyncio.Future[bool]"]`-style API folded into `async def ask(payload, emit) -> bool` — emits `{"confirm_request": payload, "confirm_id": n}` via the caller-supplied `emit` coroutine, awaits future with timeout → False; `resolve(confirm_id, approved) -> bool`; `deny_all()` (disconnect). Router: new type `confirm.response {confirm_id, approved}` → `broker.resolve`, yields nothing on success; ipc_server calls `router.on_disconnect()` (→ `deny_all`) in its finally block.
**Tests:** approve/deny resolve ask; timeout → False (monkeypatched short timeout); deny_all flushes pending; unknown confirm_id ignored; disconnect path via fake router.
- [ ] failing tests → implement → green → commit "Add the generic confirm-over-IPC broker"

### Task 12: UI confirm plumbing
**Files:** modify `lumen/ui/daemon_client.py`, `lumen/ui/__main__.py` (wire dialog at shell level); test `tests/ui/test_daemon_client.py` (extend).
**Produces:** `DaemonClient.confirm_requested = pyqtSignal(dict)` (payload incl. `confirm_id`); `respond_confirm(confirm_id: int, approved: bool)` sends `confirm.response`; `__main__` connects signal → `ConfirmDialog.ask(title, intro, rows, confirm_label)` → `respond_confirm`. Buffer parser learns `confirm_request` lines.
**Tests:** incoming line emits signal with payload; respond_confirm writes correct JSON line.
- [ ] failing tests → implement → green → commit "Surface daemon confirm requests through the UI dialog"

### Task 13: Event proposal extraction + validation gate
**Files:** create `lumen/daemon/llm/event_create.py`; test `tests/daemon/llm/test_event_create.py`.
**Produces:** `parse_proposal(text) -> dict | None` (lenient JSON extraction — first {...} block); `validate_proposal(p, *, now, user_message) -> tuple[dict | None, str | None]` — normalized proposal or (None, honest reason). Rules: title required; start/end ISO parse; end > start; duration ≤ 12 h unless all_day; start ≥ now − 5 min; attendees must be RFC-ish emails literally present (case-insensitive) in `user_message`, else rejected reason names the address; `recurrence` if present must parse via `dateutil.rrule.rrulestr` and round-trip verbatim into the confirm payload. `async propose_event(llm, message, *, now, model=None) -> tuple[dict | None, str | None]` (single non-tool structured generation, prompt embeds now+tz+weekday). `confirm_payload(p) -> dict` (icon ▲, rows Title/When/Calendar "Personal (primary)"/Location/Attendees + invite note/Repeats human + verbatim RRULE).
**Tests:** every rejection rule; attendee-in-message rule both ways; rrule garbage rejected, valid kept verbatim; confirm rows include verbatim rule + invite note; parse tolerates prose around JSON.
- [ ] failing tests → implement → green → commit "Add event proposal extraction and the mechanical validation gate"

### Task 14: Write scope + `create_event` tool + router create paths
**Files:** modify `lumen/mcp_servers/gcal.py`, `lumen/daemon/router.py`, `lumen/daemon/__main__.py`, local `lumen/config.toml` (allowlist + write tool); test `tests/mcp_servers/test_gcal.py`, `tests/daemon/test_router.py`.
**Produces:** MCP `create_event(title, start, end, all_day=False, location="", description="", attendees=(), recurrence="") -> str` using WRITE_SCOPES creds, `sendUpdates="all"` iff attendees else `"none"`, primary calendar, returns created summary line or graceful failure string. Router: `EVENT_HINT` regex (book/schedule/create/add/set up × meeting/call/event/appointment/reminder-ish) checked before `CAL_HINT`/`TOOL_HINT`; `_create_event_chat(message)`: propose → invalid ⇒ honest chunk; valid ⇒ `broker.ask(confirm_payload)` → declined ⇒ "cancelled, nothing created" chunk; approved ⇒ bridge.call create_event (tool-logged) → post-create `sync_once()` → chunk quoting result. One-shot `calendar.create {proposal}` (form path): same validation + confirm + execute, `{"result": ...}`/`{"error": ...}`.
**Tests:** hint precedence (create beats CAL/TOOL/REC on "schedule a meeting…"); declined ⇒ no bridge call; approved ⇒ call + log + sync; invalid ⇒ no confirm emitted; one-shot happy/invalid/declined; sendUpdates logic in server tests.
- [ ] failing tests → implement → green → commit "Create calendar events behind the confirm flow, NL and one-shot"

### Task 15: "+ Event" form
**Files:** modify `lumen/ui/calendar_view.py`; test `tests/ui/test_calendar_view.py`.
**Produces:** small modal form (Title, Date, Start–End time, Location) → `calendar.create` one-shot; daemon confirm dialog then arrives via Task 12 plumbing; success toast/status + grid refresh on result; validation errors shown inline.
**Tests:** form submit sends structured proposal; error result surfaces; grid re-requests after success.
- [ ] failing tests → implement → green → commit "Add the + Event form through the same confirm ritual"

### Task 16: Verification + close-out
- [ ] Full suite green; daemon boots with no token (poller idles, dashboard shows not-connected); `scripts/screenshot.py` sanity screens if usable.
- [ ] Update `.claude/skills/calendar-integration.md` (decisions: custom MCP server, window sizes, confirm broker), `mcp-integration.md` (server deviation note), `development-plan.md` (Phase 5 status + what remains: user OAuth + live verify).
- [ ] Commit "Record Phase 5 implementation notes" — live verification against real Google remains for the user's OAuth session; exact steps in the setup doc.
