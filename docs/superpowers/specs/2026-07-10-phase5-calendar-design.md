# Phase 5 — Calendar (read, then write): Design

Date: 2026-07-10. Builds on `.claude/skills/calendar-integration.md`, the Phase 3 MCP
plumbing, the `CalendarScreen`/`DashboardScreen` skeletons, and the one-shot +
context-injection patterns from Phases 2/4. Phase 4.5 (filesystem) is deferred by user
decision; the confirm-over-IPC machinery it specified is built *here* instead, in its
generic form, and 4.5/6/7 will reuse it unchanged.

Three behaviors below were design-gate questions; at the 2026-07-10 gate the user
delegated ("carry on"), so the recommended answer was adopted in each case (marked
**[decided 2026-07-10]**, alternative noted for the record).

## One-time setup (the user-facing cost)

Google requires a personal OAuth client for API access — there is no shipped shared
credential. One guided session (~10 min, step-by-step in `docs/google-oauth-setup.md`,
same spirit as `docs/ollama-setup.md`): create a Google Cloud project, enable the
Calendar API, create a Desktop OAuth client, download its JSON to
`~/.local/share/lumen/google/client_secret.json`. Then `uv run lumen-google-auth`
opens the browser consent screen once and stores the refresh token
(`~/.local/share/lumen/google/token.json`, chmod 600, never in SQLite, never committed).

Scopes are staged: `calendar.readonly` now; the write half adds `calendar.events`
(one more consent prompt when it lands). Gmail scopes wait for Phase 6 — same Google
client, same token file, no new console work then.

## User-visible behavior — read half

**Dashboard.** The center column becomes today's real calendar: time-positioned event
blocks (range auto-widens beyond 08:00–20:00 if an event falls outside), a now-line,
all-day events pinned at the top, and the real "synced Nm ago" stamp from the poller.
The todos column goes live too via the existing `todos.list` one-shot (nearly free,
and a fixture column next to a live one reads as broken). The mail column keeps its
fixture data but is visibly labeled as a Phase 6 placeholder — no pretend-live data.

**Calendar screen.** The month grid renders real events from the local cache: correct
month math (prev/next/Today navigation), today highlighted, up to 3 event chips per
cell with "+N more". The hardcoded work/personal/health/social legend is replaced by
the actual Google calendars, one color chip each, using Google's calendar colors.
The Week/Day segment buttons are removed until a phase builds them — no dead controls.
Days outside the synced window (past 30 / future 60 days by default, configurable)
render dimmed with a one-line "outside synced range" note when such a month is shown.

**[gate] Which calendars:** recommended — all non-hidden calendars on the account
(work/personal/shared), so "what's on my calendar" is never silently missing one.
Alternative: primary calendar only.

**Chat.** A calendar keyword hint (calendar/meeting/event/schedule/free/busy/agenda…)
injects cache context the way `TODO_HINT` does: current date *and time* with the local
timezone, then one compact line per event for today through +14 days, with an explicit
bounds statement ("nothing outside this range is shown — say so if asked") and an
explicit empty marker. This answers "what's on my calendar today/this week", "am I
free at 3pm Thursday", and basic meeting questions (attendees/location come along per
line). Questions beyond the injected window can reach the live `list_events` MCP tool
via the existing tool loop; full meeting prep (cross-referencing email) stays Phase 8.

**Poller.** The daemon's first background poller: a plain asyncio task refreshing the
whole rolling window every 5 minutes (config `calendar_poll_minutes`, values under 5
rejected — the no-tight-loops constraint), Google API client directly, never the
LLM/MCP loop, recurring events expanded to instances (`singleEvents`). Not
authenticated yet → the poller idles; dashboard and calendar screens show a "Google
Calendar not connected — see setup" state instead of fake data. Poll failure → keep
the stale cache, surface staleness via the synced-ago stamp.

## User-visible behavior — write half

**NL event creation.** "Book a call with Sam Friday afternoon" → the router's
create-intent hint routes to a dedicated pipeline (the `book_recs` pattern): the fast
model extracts a structured proposal (title, start/end, location, description,
optional recurrence, optional attendees) with the current date/time/timezone as
context; a mechanical validation gate rejects garbage *before* the user ever sees it
(missing title, unparseable or past start, end ≤ start, absurd duration, malformed
recurrence rule, attendee that isn't a literal email address present in the user's own
message). A validation failure is an honest chat reply asking for the missing
specifics — never a silent guess.

**The confirmation (the safety checkpoint).** A valid proposal pauses daemon-side and
emits a `confirm_request` over IPC; the UI raises the existing hardened
`ConfirmDialog` showing exactly what will be created: Title, When (rendered local),
Calendar, Location, Attendees, and — if recurring — a human-readable summary *plus the
recurrence rule verbatim* (per calendar-integration.md: never confirm a recurrence
implicitly). Only an explicit confirm executes the MCP `create_event` tool (logged in
`tool-calls.jsonl` like every tool call); the reply quotes what the API actually
created. Decline → nothing created, honest "cancelled" reply. Timeout (120s) or UI
disconnect → deny, so no gated call hangs forever. Events are created on the primary
calendar in v1.

**[gate] Attendees & invites:** recommended — attendees are attached only when the
request contains a literal email address (Lumen has no contacts source yet, so
"with Priya" cannot resolve to an address; the name goes in the title instead), and
Google emails those attendees an invite on create, with the dialog stating plainly
"An invite will be emailed to: …". Alternative: no attendee support in v1 (self-only
events).

**[gate] "+ Event" button:** recommended — the month screen's button opens a small
form (title, date, start–end, location) whose submit goes through the *same* daemon
validation + confirmation ritual (consistency beats shaving a click, per the Phase 6
note). Alternative: defer the form; button prefills the launcher with NL text.

**Confirm-over-IPC (new machinery, deliberately generic).** A daemon-side broker:
`await broker.ask(payload) → bool`. The pending request's event stream carries
`{"confirm_request": {icon/title/intro/rows/confirm_label}, "confirm_id": N}`; the UI
answers with a `confirm.response {confirm_id, approved}` message; the broker resolves
the awaiting future, defaulting to deny on timeout/disconnect. Nothing in it is
calendar-specific — payload rows are caller-supplied — so Phase 4.5 file writes and
Phase 6/7 mail writes plug in without rework.

## Data

```sql
CREATE TABLE IF NOT EXISTS events (
    id TEXT NOT NULL,                       -- Google event/instance id
    calendar_id TEXT NOT NULL,
    calendar_name TEXT,
    color TEXT,                             -- calendar-level color, hex
    title TEXT,
    start_at TEXT NOT NULL,                 -- RFC3339 as given; ISO date if all-day
    end_at TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    description TEXT,
    attendees TEXT NOT NULL DEFAULT '[]',   -- JSON [{email, name, self}]
    status TEXT,                            -- confirmed | tentative
    PRIMARY KEY (calendar_id, id)
);
CREATE TABLE IF NOT EXISTS sync_state (     -- shared with Phase 6 email sync later
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Timezone rule: store exactly what the API returns (offset-carrying datetimes, or bare
dates + `all_day` for all-day events); render local at the edges; compute "today" and
free/busy in the local zone with aware datetimes — DST changes then take care of
themselves. Whole-window replace per poll (one transaction), no diffing — the dataset
is small by design.

## Architecture

- **`daemon/connectors/gcal.py`** — `EventStore` (window queries for UI/context,
  transactional window replace, last-sync accessors via `sync_state`) and
  `CalendarSync` (Google API client: calendar list + per-calendar events pull;
  one `sync_once()` the poll task loops on). A shared
  **`daemon/connectors/google_auth.py`** owns client-secret/token paths and credential
  refresh — Phase 6 Gmail reuses it as-is.
- **MCP server: custom in-repo `lumen/mcp_servers/gcal.py`** (FastMCP,
  `openlibrary.py` template): `list_events(start, end)` read tool now;
  `create_event(...)` joins the allowlist only when the write half lands. *Deviation
  from the plan's `mcp-google-workspace` default, decided 2026-07-10:* the poller
  already talks to the API directly per the bulk-sync constraint, so the community
  server would add a second OAuth stack, a second consent, and coupling to its token
  format — the thin custom server shares `google_auth.py` and keeps the write surface
  explicit. Record in `mcp-integration.md` at close-out.
- **Confirm broker** — `daemon/confirm.py`, generic as described above; router owns
  one instance; `ipc_server` learns to route `confirm.response` messages to it.
- **Router additions** — `calendar.list {from,to}` one-shot (events + connected flag +
  last-sync; dashboard and month grid both use it); `calendar.create` one-shot (form
  path, same validation + confirm ritual); `CAL_HINT` context injection; create-intent
  hint routed *before* the generic `TOOL_HINT` (precedence tested). NL extraction is a
  single structured generation on the fast model — no tool chain, so the 14B
  escalation question stays deferred (it belongs to 4.5's benchmark task).
- **UI** — `dashboard.py` and `calendar_view.py` go live via `daemon_client.request`
  (todo-manager pattern: load on open, offline banner, no business logic);
  `DaemonClient` gains a `confirm_requested` signal and a
  `respond_confirm(confirm_id, approved)` sender wired to `ConfirmDialog`.
- **Config** — `[google] client_secret_path/token_path` overrides; `[sync]`
  `calendar_poll_minutes = 5` (reject < 5), `calendar_window_past_days = 30`,
  `calendar_window_future_days = 60`. New deps: `google-api-python-client`,
  `google-auth-oauthlib`, `python-dateutil` (recurrence-rule validation).

## Sequencing

Read half first, live-verified against the dev-plan success criterion (dashboard shows
today's real events) before any write code lands. Write half then adds the
`calendar.events` scope, the confirm broker, the pipeline, and `create_event` —
live-verified by proposing an event via chat and confirming nothing is created without
an explicit confirm (and that a decline creates nothing).

## Testing

- `EventStore`: window replace, range queries, all-day + offset handling, last-sync.
- `CalendarSync.sync_once()` against fake API payloads (multi-calendar, recurring
  instance expansion, all-day, empty).
- Context builder: bounds statement, empty marker, timezone rendering, DST edge
  ("today" around a transition).
- Proposal validation gate as pure unit tests: every rejection reason above, plus the
  attendee-email-must-appear-in-message rule.
- Confirm broker: approve, deny, timeout→deny, disconnect→deny; `confirm.response`
  routing through a fake IPC client.
- Router: `calendar.*` one-shots, hint precedence (create-intent vs `CAL_HINT` vs
  `TOOL_HINT` vs `REC_HINT`), not-connected results.
- UI: both screens render from daemon data + offline/not-connected states; confirm
  dialog round-trip (pytest-qt, existing patterns).
- Live verification (phase gate, manual): the two success criteria above, real Google
  account, real Ollama, over the real socket.

## Out of scope (Phase 5)

- Editing/deleting existing events (NL or UI); accepting/declining invites (never
  auto, and not even manually this phase); other people's free/busy; secondary-source
  contacts (why attendees require literal emails); Week/Day calendar views; the
  `ui_v2` visual rebuild (untouched — Phase 10 territory); Gmail anything (Phase 6);
  14B escalation benchmark (stays with Phase 4.5).
