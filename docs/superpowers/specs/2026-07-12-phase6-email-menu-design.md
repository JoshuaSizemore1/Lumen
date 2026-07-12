# Phase 6 — Email menu (local mirror, browse/search, manage): Design

Date: 2026-07-12. Builds on `.claude/skills/email-menu.md` (schema + two-path sync
strategy), `email-integration.md` (scopes, write flow), the Phase 5 sync-worker and
Google-auth machinery (`connectors/gcal.py`, `connectors/google_auth.py`), the generic
confirm-over-IPC broker (`daemon/confirm.py`), and the live `ui_v2` MailScreen skeleton.
Send is Phase 7 — nothing in this phase composes or sends mail.

Three behaviors below were design-gate questions; at the 2026-07-12 gate the user
delegated ("start the phase 6 implementation"), so the recommended answer was adopted
in each case (marked **[decided 2026-07-12]**, alternative noted for the record).
Everything else is either settled in the skill files or a technical choice per
CLAUDE.md decision autonomy.

## One-time setup (the user-facing cost)

The same Google setup from Phase 5 (`docs/google-oauth-setup.md`) — still pending.
This phase adds the Gmail scopes to that consent: `gmail.readonly` (sync/browse/search)
and `gmail.modify` (archive / mark read / labels — the manage actions built this
phase). `gmail.send` waits for Phase 7, per the staged-scopes rule. Since the Phase 5
consent hasn't been run yet, it's still **one** browser consent covering Calendar +
Gmail together — no extra console work, same token file.

Until connected, the Mail screen shows the same "not connected — see setup" state the
calendar screens use. No fake data.

## User-visible behavior

**First sync (bounded, resumable).** On first connect, a background worker pulls the
last **6 months** of mail — headers, full bodies, labels, read state — into a local
SQLite mirror. It's paginated with the cursor saved after every page, so closing the
app mid-sync resumes where it left off instead of restarting. A quiet progress line on
the Mail screen ("syncing — N so far") while it runs; the screen is usable with
whatever has landed. Everything except Spam and Trash is mirrored — including archived
mail — so search covers your whole recent history, not just the inbox.

**[decided 2026-07-12] Initial window:** recommended — **6 months** (config-adjustable later;
raising it backfills the older range in the same resumable way). Alternative: 12
months — deeper search from day one, at the cost of a first sync roughly twice as
long. Both are bounded; "everything ever" stays rejected per project scope.

**Staying current.** After the first pull, syncs are deltas via Gmail's History API —
only new mail, deletions, and label/read-state changes move, so a poll is a few tiny
requests, not a re-list. Runs on app open, on the Mail screen's manual refresh button,
and on the daemon's 5-minute timer (same cadence discipline as calendar). If Gmail
expires the saved history position (it does after long offline stretches), the worker
quietly re-baselines the bounded window instead of erroring.

**The Mail screen (the "manage it yourself" surface).** The existing two-pane
`ui_v2` MailScreen goes live from the local mirror:

- **List pane**: sender, subject, snippet, time, unread shown bold; Inbox by default
  with an Unread filter; newest first, paged as you scroll.
- **Search**: a search bar over *everything mirrored* — full-text across subject,
  body, and snippet, plus a sender match — answered instantly from the local
  full-text index. No Gmail API call per keystroke, works offline.
- **Reading pane**: the full message rendered as plain text (no remote images or HTML
  tracking pixels by design — privacy over fidelity in v1). Attachment names are
  listed; downloading attachments is out of scope this phase.
- **Manage actions**: Archive and Mark read/unread buttons on the open message. Each
  goes through the standard confirmation overlay before touching Gmail (settled in
  `email-menu.md`: these are writes; consistency beats shaving a click). On confirm
  the change hits Gmail *and* the mirror immediately — no waiting for the next poll.

**[decided 2026-07-12] Reading an email:** recommended — opening a message in Lumen does **not**
mark it read in Gmail. Read-state only changes when you explicitly hit "Mark read"
(confirmed, like every write). This keeps "no silent writes" intact — browsing your
mirror never touches your account. Alternative: auto-mark-read on open behind a
settings toggle (one blanket consent) — more like a normal mail client, but it makes
reading a write.

**[decided 2026-07-12] Manage-action set for v1:** recommended — **Archive + Mark read/unread**
now; label *editing* deferred (labels are mirrored and searchable, there's just no
"apply label" picker yet — it lands with a later polish pass or alongside Phase 7).
Alternative: include a minimal label picker now, at the cost of extra UI surface in
the heaviest phase.

**Dashboard.** The mail column drops its Phase-6-placeholder label and shows real
unread mail (sender, subject, time) straight from the mirror. *Deviation from the
dev-plan's step 5, decided 2026-07-12:* no separate "lightweight metadata cache" is
built — the mirror already holds exactly this data, and a cheap indexed query
(`unread, newest first, limit N`) serves the dashboard. A second cache would mean a
second sync path to keep honest, for no user-visible gain. Record in
`email-integration.md` at close-out.

**Chat.** Two additions, following the Phase 5.5 grounding pattern:

- **Context injection** (`MAIL_HINT`, like `CAL_HINT`): questions shaped like "any
  new email?", "did anything come in from Sam?" get a compact unread/recent summary
  injected from the mirror — with the same explicit bounds statement and empty marker
  the calendar context uses.
- **Local search tools**: "find that email from X about Y" reaches a new in-repo MCP
  server (`lumen/mcp_servers/mail.py`, same FastMCP pattern as the gcal server)
  exposing read-only `search_email` / `get_email` backed by the mirror's full-text
  index — fast, offline, zero API quota, and grounded (answers trace to real tool
  results in `tool-calls.jsonl`). Mail grounding is carried whenever the tool loop is
  active, per the Phase 5.5 rule — not gated on a per-turn keyword.

A live Gmail MCP connector (for time-sensitive queries and sending) is deliberately
deferred to Phase 7 — the mirror is at most 5 minutes stale, which covers Phase 6's
question shapes.

## Data

`emails` + FTS5 index + the existing `sync_state` table, per `email-menu.md`:

```sql
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,            -- Gmail message id
    thread_id TEXT,
    sender TEXT,                    -- display form: Name <addr>
    recipients TEXT,
    subject TEXT,
    body TEXT,                      -- plain text (text/plain part, else stripped HTML)
    snippet TEXT,
    labels TEXT,                    -- comma-separated Gmail label ids
    received_at TEXT,               -- RFC3339
    is_read INTEGER,
    attachments TEXT NOT NULL DEFAULT '[]'   -- JSON [filename, …]; names only
);
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject, body, snippet, sender,
    content='emails', content_rowid='rowid'
);
-- + insert/update/delete triggers keeping emails_fts in step with emails
```

`sync_state` rows: `gmail_history_id`, `gmail_bulk_cursor` (page token; cleared when
the bounded pull completes), `gmail_bulk_window_months`, `gmail_last_sync`. Same
`lumen.db` file — already `chmod 600` (the privacy note in `email-menu.md` is
satisfied by the existing lockdown; encryption-at-rest stays out of scope).

Deletions: History API `messagesDeleted` removes rows (and FTS entries). Re-baseline
after an expired historyId re-lists the window and prunes rows no longer present.

## Architecture

- **`daemon/connectors/email_menu.py`** — `EmailStore` (list page / search / unread
  panel / upsert / delete / sync-state accessors) + `GmailSync` (bounded bulk pull
  with resumable pagination, History-API incremental, expired-history re-baseline,
  one `sync_once()` a poll task loops on). Mirrors `gcal.py`'s Store/Sync split
  exactly: blocking Gmail API calls via `asyncio.to_thread`, SQLite writes on the
  loop thread. Plain background job — **never** the LLM/MCP loop (the skill's NOT #1).
- **`connectors/google_auth.py`** — Gmail scopes join `SCOPES`; the existing staged-
  consent check already returns not-connected until the (re-)consent includes them.
- **Router** — one-shots: `emails.list {filter, page}`, `emails.search {query}`,
  `emails.get {id}`, `emails.unread {limit}` (dashboard), `mail.refresh` (manual
  sync trigger); gated writes `emails.archive {id}` / `emails.mark_read {id, read}`
  through the existing `ConfirmBroker` ritual, executing via the Gmail API client
  (same client the sync worker uses — a write this small doesn't need the MCP hop),
  then updating the mirror. `MAIL_HINT` context injection; mail grounding carried on
  `tool_loop=True` per Phase 5.5.
- **MCP server: `lumen/mcp_servers/mail.py`** — read-only `search_email` /
  `get_email` against the mirror (FastMCP, gcal template). Read-only allowlist; no
  write tools here (writes are UI-confirmed one-shots, not model-initiated, in v1).
- **UI (`ui_v2`)** — `AppState` gains `refresh_mails` / search / action senders
  (todo/chat patterns); MailScreen gains the search bar, unread filter, paging,
  syncing/not-connected states; Archive & Mark-read wire to the confirm overlay
  (`confirm_id` round-trip already built). No business logic UI-side.
- **Config** — `[sync]` gains `gmail_poll_minutes = 5` (reject < 5),
  `gmail_window_months = 6`. No new dependencies — the Google API client from
  Phase 5 covers Gmail.

## Sequencing

Schema + store first, then the bulk worker, then incremental sync (each fully tested
before the next — the sync engine is the phase's heart and the UI is blind without
it), then the live Mail screen + dashboard panel, then chat (`MAIL_HINT` + MCP search
tools), manage actions last (they need scope + confirm plumbing end-to-end). Live
verification against the real mailbox needs the one-time Google setup done.

## Testing

- `EmailStore`: upsert/replace, list paging + unread filter, FTS search (incl.
  trigger sync on update/delete), unread-panel query, sync-state round-trip.
- `GmailSync` against fake API payloads (gcal test pattern): bounded first pull with
  mid-sync interruption → resume from cursor; incremental adds/deletes/label flips;
  expired-historyId 404 → re-baseline not crash; body extraction (text/plain,
  HTML-only, multipart, attachments).
- Router: every `emails.*` one-shot; archive/mark-read confirm round-trip (approve →
  API call + mirror update; decline → nothing); `MAIL_HINT` injection bounds/empty
  marker; grounding carried on tool-loop turns.
- MCP server: `search_email`/`get_email` against a seeded mirror.
- UI: list/search/reading pane from daemon data; not-connected, syncing, and empty
  states; confirm overlay round-trip for both actions (offscreen Qt, existing
  patterns).
- Live verification (phase gate, manual): the dev-plan criteria — mailbox mirrored
  and searchable offline; reopen pulls only deltas (verify via request counts/log);
  interrupted first sync resumes; archive/mark-read prompt and then really change
  Gmail.

## Out of scope (Phase 6)

Compose/send/reply-sending and drafts (Phase 7 — the existing Reply button stays
wired to its placeholder confirm); attachment download; HTML rendering / remote
images; label editing (if the gate lands as recommended); auto-mark-read on open
(ditto); encryption at rest beyond chmod 600; a live Gmail MCP connector; push
notifications (rejected in scope); threading/conversation view (messages listed
individually in v1).
