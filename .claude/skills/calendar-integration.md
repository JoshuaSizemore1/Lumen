# Skill: Calendar Integration (Google Calendar)

## Implemented (Phase 5, 2026-07-10) — decisions that stick
- **MCP server is the custom in-repo `lumen/mcp_servers/gcal.py`** (FastMCP), not
  the community `mcp-google-workspace`: the poller already uses the Google API
  client directly (bulk-sync constraint), so the custom server shares one OAuth
  stack via `daemon/connectors/google_auth.py` — one consent, one token file.
  Tools: `list_events` (model-visible), `create_event`/`delete_event` (daemon-gated; see below).
- **Auth**: installed-app flow via `uv run lumen-google-auth`
  (`docs/google-oauth-setup.md` is the user walkthrough). Token at
  `~/.local/share/lumen/google/token.json`, chmod 600. `google_auth.SCOPES`
  is what the script requests; Phase 6 adds Gmail scopes to the same module.
- **Sync window**: past 30 / future 60 days (config `[sync]`,
  `calendar_window_past_days` / `calendar_window_future_days`), whole-window
  replace per poll, `singleEvents=True` so recurring events arrive expanded.
  All non-hidden calendars are synced (user decision — never silently miss the
  shared calendar). Poll floor: `calendar_poll_minutes < 5` is rejected at load.
- **Confirm-over-IPC** (`daemon/confirm.py`, `ConfirmBroker`): the write path
  yields a `confirm_request` on its own stream and awaits `confirm.response`
  arriving on a *different* connection (the shell keeps a dedicated
  DaemonClient for replies — a connection paused mid-request can't read its own
  socket). Timeout 120s and disconnect both deny. Phase 4.5/6/7 reuse this as-is.
- **Write safety is mechanical**: `router.WRITE_TOOLS` filters write-capable
  tools out of the generic tool loop, so the model can never call
  `create_event` itself; only `_gated_create` (post-confirm) can. `created` is
  reported true only when the server answered `Created:` — transport success
  alone doesn't count.
- **Event deletion (added 2026-07-13, live-verified)**: `calendar.delete`
  one-shot ({id, calendar_id}) — the daemon looks the event up in its cache,
  shows the standard confirm dialog (title/when/calendar, plus "attendees will
  be notified" when any non-self attendee exists → `sendUpdates=all`), and only
  then calls the gated `delete_event` tool; only a `Deleted:` reply counts,
  and success triggers an immediate re-sync so the cache drops the event. UI
  affordance: ✕ on the day-view agenda rows (hidden in sample mode — sample
  events carry no id). Chat/NL deletion deliberately not wired — deleting by
  fuzzy description is riskier than a click; revisit only if asked.
- **NL creation** (`daemon/llm/event_create.py`): fast-model JSON extraction +
  a validation gate (future times, ≤12h unless all-day, RRULE must parse and is
  shown verbatim in the dialog, attendees only when the literal email address
  appears in the user's message — Lumen has no contacts source, and invites are
  emailed with that stated in the dialog). The "+ Event" form on the month
  screen goes through the same `calendar.create` → confirm ritual.

## Auth
Reuse the same OAuth2 credentials/flow as email where possible (Google supports combined scopes in one consent).

## Scopes
- `calendar.readonly` for viewing/summarizing
- `calendar.events` (write) only once event creation/editing is actually implemented

## Sync strategy
- Poll for events in a rolling window (e.g. today + next 14 days) on the same interval as email sync.
- Cache events in SQLite: title, start/end, location, attendees, description. Refresh the window on each poll rather than doing incremental diffing — the dataset is small enough that this is simpler and cheap.

## Write actions
Same pattern as email: router proposes (e.g. "create event 'Dentist' Tuesday 2pm"), UI shows exact details, user confirms, connector executes. No silent calendar writes.

## What the LLM should be able to do
- "What's on my calendar today/this week"
- "Am I free at 3pm Thursday"
- Natural-language event creation ("book a call with X Friday afternoon") → proposed event → confirm → create

## What NOT to do
- Don't auto-accept/decline invites.
- Don't create recurring events without explicit confirmation of the recurrence rule — recurrence mistakes are annoying to unwind.
