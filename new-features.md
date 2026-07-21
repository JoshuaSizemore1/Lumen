# Lumen — new feature ideas

Living backlog + design of record. First section is the committed design for
the mail batch; after it comes the general feature backlog (pulled from the
in-app todo list); the parking lot at the bottom holds ideas we've named but
are **not** building yet.

---

## Design of record — Inbox sorting, syncing & rules (2026-07-15)

### Context — what already exists (don't rebuild)
The shipping UI is `lumen/ui_v2`. The Mail backend (`daemon/connectors/email_menu.py`)
is fully built: bounded bulk sync, History-API incremental deltas, archive,
mark-read (Gmail-side **and** local), send/reply, local FTS search.

- **Refresh button** already exists (↻ in the inbox header) and runs a real
  Gmail History-API sync via the `mail.refresh` IPC op. → verify, don't rebuild.
- **Gmail → Lumen read/label sync** already works (incremental poll pulls
  read-state and label changes).
- **Explicit "Mark read"** already propagates to Gmail.

What's missing is everything below.

### Behavior decisions (locked with the user)
- **Auto-mark read on open**, propagating to Gmail (reverses the old "browsing
  never changes read state" gate — update that note in `email-menu.md`).
- **Rules run automatically; the LLM only runs on explicit request** (respects
  the power/thermal constraint).
- **Default inbox view = `INBOX` label only.** Labeled mail has left the inbox.
- **Labeling = moving:** applying any label also removes `INBOX` (in Gmail too).
- **Sort UI = filter chips + colored label tags on rows.**

---

### A. Refresh button — verify pass (no new feature)
**Verified 2026-07-15.** ↻ → `refresh_inbox` → `mail.refresh` (now
scope-preserving) → sync; covered by daemon + UI tests.
Confirm the ↻ button drives an end-to-end sync and the status line
("syncing… / synced <time>") reflects it. Fix if broken. No build.

### B. Auto-mark-read on open → Gmail
**Built 2026-07-15.**
When a message is selected and stays open **~1s** (dwell timer, so
arrow-keying past mail doesn't mark everything), mark it read locally **and**
call Gmail to remove `UNREAD`. Already-read messages are a no-op. Reuses the
existing `mark_read` path — genuinely two-way.

- Dwell timer lives in the Mail screen; fires `set_mail_read(id, read=True)`
  only if the message is still selected and still unread.
- Removes `UNREAD` only. Does **not** touch `INBOX` (that's labeling, § D).

### C. Label sorting UI — filter chips + tags
**Built 2026-07-15.**
- **Chip row** under the "Inbox" header: **All · Unread · <one chip per label>**.
  - **All** (default, nothing selected) → local DB query scoped to `INBOX`.
  - **Unread** → `INBOX` + `UNREAD`.
  - **<Label>** → local DB query for that label (this mail no longer has
    `INBOX`, so it isn't in the default list — chip selection **re-queries the
    local DB by scope**, it is not a client-side filter of the loaded list).
  - All queries hit the local DB only — offline, no Gmail call, no quota.
- **Label tags**: small colored pills on each message row and in the reading
  pane. Each label gets a stable color (hash label name → palette slot).
- Labels are already synced into the DB; expose them through the existing
  `emails.list` payload (add `labels: [...]` per message + a top-level list of
  labels present, for building the chip row). Gmail's system labels
  (`INBOX`, `UNREAD`, `SENT`, `CATEGORY_*`, etc.) are filtered out of the
  user-facing chip row — only user labels show.

### D. Rules engine (instant, no LLM)
**Built 2026-07-15** (incl. D.1 label plumbing, D.2 three creation paths,
D.3 rule-authoring prompt).
Deterministic rules that run in the daemon on each sync for **new** mail —
zero model cost.

- **Matchers** (a rule matches if **any** of its conditions hit):
  - `from` address (exact)
  - sender `domain`
  - `subject` contains keyword
  - `body` contains keyword  ← needed for "relating to X" concepts
  - keyword lists are OR'd; matching is case-insensitive substring.
- **Action:** apply target label + **remove `INBOX`** (move), locally and in
  Gmail. If the target label doesn't exist in Gmail, create it.
- **Storage:** rules table in the app SQLite DB (not TOML — user isn't
  technical). Schema roughly:
  `id, label, from_addrs, domains, subject_kw, body_kw, enabled, created_at`.
- **Where rules run:** in `email_menu.py` sync path, applied to newly-upserted
  INBOX messages before the UI reads them. Applying a label = one Gmail
  `modify` (`addLabelIds`, `removeLabelIds:[INBOX]`) + local `update_labels`.
- **Pre-authorization:** a rule's writes are pre-approved when the rule is
  created (see § D.2) — the whole point is hands-off. Per-email confirmation
  would defeat it.

#### D.1 Gmail label plumbing (new connector surface)
- Fetch the user's Gmail label list (name ↔ id map); cache it, refresh on sync.
- `create_label(name)` when a rule/suggestion targets a missing label.
- Generalize the existing INBOX/UNREAD-only modify into
  `apply_label(mid, label_name)` = add label id + remove `INBOX`, Gmail + local.

#### D.2 Rule creation — three paths
1. **Conversational, via the local model (the "skill" — see § D.3).**
   "Create a rule to filter all emails relating to boy scouts as BSA."
2. **From an open email** — "Always label like this…" prefilled with the
   sender → pick/create a label.
3. **Settings editor** — list of rules, edit/enable/disable/delete.

All three converge on the same confirm step: show the parsed rule, offer
**"apply to the N matching emails already in your inbox too?"**, then save.
That confirmation is the pre-authorization for the rule's future auto-applies.

#### D.3 Local-model rule-authoring prompt (the "skill")
A guided prompt asset for the **local Ollama model** (distinct from the
`.claude/skills/` files, which are for Claude Code). Lives as a prompt asset in
`daemon/llm/` and is wired as a router intent so a natural-language "create a
rule…" request routes here instead of a normal chat/tool turn.

It teaches the small local model to turn a fuzzy request into a concrete rule:

- **Input:** the user's sentence + the list of the user's existing Gmail labels.
- **Job:** expand the concept into matchers and pick/create the target label.
- **Output:** a structured rule (JSON) matching the § D schema.
- **Few-shot anchor** — "filter all emails relating to boy scouts as BSA" →
  ```json
  {
    "label": "BSA",
    "create_label_if_missing": true,
    "subject_kw": ["boy scout", "cub scout", "scouting", "troop", "pack ", "BSA"],
    "body_kw":    ["boy scout", "cub scout", "scouting", "troop", "BSA"],
    "domains":    ["scouting.org"]
  }
  ```
- The router renders this back to the user in plain language for the § D.2
  confirm step before anything is saved or applied.
- Runs **only** when the user explicitly asks to create a rule, then unloads —
  consistent with the power/thermal budget.

### E. "Suggest labels" — LLM, on request only
**Built 2026-07-15** — with one deviation: per-message model verdicts instead
of one batched call (4B model loses track of large batches; the 2026-07-13
triage lesson). Still one button press → one model load → unload.
- A button that classifies the **unlabeled** inbox mail in **one batched**
  local-model call against the user's existing Gmail label names, then shows a
  suggested label as a **one-tap chip** per email.
- Nothing is written to Gmail until you tap to accept. Accepting runs the same
  `apply_label` (label + remove `INBOX`). Model runs only on press, then unloads.
- Builds directly on § D's label plumbing.

---

### Build sequence
1. **B + C** — auto-mark-read + chip/tag UI with INBOX-only default. Visible
   wins, low risk, no LLM.
2. **D + D.3** — rules engine, Gmail label-apply-and-remove-INBOX plumbing,
   conversational creation via the local-model prompt, Settings editor.
3. **E** — "Suggest labels" button (reuses D's plumbing).

### Touches
- `lumen/ui_v2/screens/mail.py` — dwell timer, chip row, label tags.
- `lumen/ui_v2/state.py` — expose labels, scoped label queries, rule calls.
- `lumen/ui_v2/screens/settings.py` — rules editor.
- `lumen/daemon/connectors/email_menu.py` — label plumbing, rule application in
  sync path, `emails.list` payload adds labels.
- `lumen/daemon/router.py` — rule intents/one-shots, "suggest labels" turn.
- `lumen/daemon/llm/` — rule-authoring prompt asset.
- new: rules table (app DB).
- `.claude/skills/email-menu.md` — update the read-state and label gates.

### Out of scope for this batch
- Deleting mail; managing Gmail's own label hierarchy/nesting.
- Two-way "keep in inbox but also label" (Lumen's model is label = move).
- Push notifications / real-time sync (polling stays).

---

## Feature backlog — pulled from Lumen's in-app todos (2026-07-16)

Each entry cites its source todo id (the originals are still in the app's
todo list). Bug-sized items went to `todo-fixes` instead. Items 6–7 push
Lumen toward a local file workbench — worth a deliberate scope check against
`project-scope.md` before committing to them.

### 1. Chat-first main window; launcher goes hotkey-only (todo #3)
When the full app is open, the Launcher tab duplicates what the chat screen
does. Remove Launcher as a tab — the quick launcher stays, but purely as the
global-hotkey palette. Chat becomes the first tab, styled like Claude's site:
a prominent **New chat** button, a list of past chats to reopen, and a clean
empty-state prompt box for a fresh chat.
**Depends on:** the chat-session fixes (`todo-fixes` entries 5–6) — new-chat
UX is meaningless until sessions actually isolate context.

**Built 2026-07-17.** Launcher removed as a tab (`main.py`); the palette lives
only in the frameless hotkey overlay (`app.py`). Chat is tab 1 with its existing
sidebar / New chat / empty-state; tabs renumbered `1`–`5` = Chat/Dashboard/
Calendar/Todos/Books, Mail keeps its unread badge and Settings the gear.

### 2. File writing — Lumen can create and edit .md / text files (todo #4)
New capability: "write me a markdown file summarizing X" produces a real
file. Treat file writes like email sends — a write action behind explicit
confirmation (path + content preview) via the existing write-gate. Default
writes go to a configurable notes directory (e.g. `~/Documents/Lumen`);
anything outside it needs a stronger confirmation. New router intent + prompt
asset in `daemon/llm/`. Also resolves the markdown→calendar misroute
(`todo-fixes` entry 3).

**Built 2026-07-17.** Prompt asset `daemon/llm/file_write.py` (sentinel-line
output — `FILENAME:` / optional `PATH:` / `---` / raw body — not JSON, since a
4B loses newline-escaping inside JSON strings, same lesson as the triage
batch); mechanical `validate_file` gate rejects traversal/folders/empty. Router
`FILE_WRITE_HINT` fires a dedicated `_write_file_chat` (checked before COMPOSE,
guarded off `RULE_HINT`) that authors the whole document in one local-model
generation, then confirms through the existing write-gate payload — louder
intro + ⚠ icon outside the notes folder, overwrite-aware title. Approving grants
the exact path (`WriteGate.grant`) so a later fs-tool edit won't re-ask. Default
target is the notes Q&A folder (`[notes] write_dir` overrides) so written files
are immediately searchable. **Deviation:** a message naming an explicit path
(`EXPLICIT_PATH`) still routes to the fs tool loop, which writes that exact
path/content verbatim; the dedicated route owns only "author me a document"
requests with no path — which is where the markdown→calendar misroute lived.

### 3. Web lookup (todo #5)
Lumen can search online when local knowledge isn't enough. Via a search MCP
server (stack convention — no hand-rolled scraper). Triggers: the user
explicitly says "look up / search online", or the router decides the question
needs fresh facts. Answers summarize results **with source links** so the
user can tell looked-up from remembered. Power budget: one search + one
summarize turn, then unload.

### 4. Suggest-labels v2 — clearer UI + smarter picks (todo #12)
Two halves:
- **UI:** the current suggestion chips are unclear. Move to an explicit
  review pass: each suggested message shows its proposed label with
  accept / reject, plus "accept all for this label". Nothing writes until
  accepted (unchanged).
- **Accuracy:** observed misses — a Troop 148 email labeled TODO instead of
  BSA, and a Lumen test email labeled BSA. Give the model a one-line
  description per label (derived from mail already under that label), rank
  specific user labels above generic buckets like TODO when both fit, and
  add a confidence floor — below it, suggest nothing rather than guess.

**Built 2026-07-19.** Accuracy: each label now carries a one-line
description derived **mechanically** from mail already filed under it
(sender domains + example subjects via `label_suggest.describe_label`, 3
rows per label from the local mirror — no extra model cost); the prompt
tells the model to prefer topic-specific labels over generic buckets; and
the verdict format is `{"label", "fit": "strong"|"weak"}` where only a
**strong** fit survives `parse_verdict` — weak, missing, unknown, or null
all suggest nothing rather than guess. UI: per-row "→ label" pill with
✓ accept / ✕ reject, plus a review bar ("✨ N suggestions") with one
"Accept all <label> (n)" chip per proposed label and "✕ Dismiss all";
reject/dismiss are purely local. Live-verified against the real inbox:
both observed misses are gone — the Troop 148 packing-list mail now gets
BSA, the Lumen test email gets no suggestion, and 13 of 15 scanned
messages honestly stayed unsuggested instead of being guessed into TODO.

### 5. Auto-refresh mail on open + while open (todo #13)
Three triggers: app launch → immediate `mail.refresh`; opening the Mail tab
→ refresh; every 5 minutes while the app is open. The daemon's 5-minute
background poll should already cover the last one — verify rather than
rebuild; the new work is the two open-triggered refreshes, a visible
"synced <time>" status, and a debounce (min ~60s between syncs) so
tab-flipping doesn't hammer Gmail.

**Built 2026-07-17.** Confirmed the daemon's 5-min poll already covers the
periodic case (verified, not rebuilt). Added launch- and Mail-tab-open Gmail
delta-syncs (`AppState.sync_inbox`) sharing one 60s debounce window with the
manual ↻; inside the window a tab-flip re-reads the local mirror instead. The
Mail screen also re-queries the mirror every 5 min while visible.

### 6. File browser screen with context-aware prompting (todo #16)
A new screen for browsing local directories, with a prompt box on the same
screen. Asking a question there auto-loads the current directory as context
(file names/types/sizes; contents of small text files on request) so Lumen
answers with real knowledge of what's in front of you. Read-only in v1 —
browsing and asking, no file operations. Stepping stone to item 7.

**Built 2026-07-19** together with item 7 as one **Files** tab (key `6`) —
spec: `docs/superpowers/specs/2026-07-19-files-workbench-design.md`, skill:
`file-workbench.md`. Browser column (path bar + ⌂/↑, folders-first listing)
is UI-local via `daemon/connectors/local_files.py`; asks ride the normal
`chat` op with `cwd`/`open_file` payload keys → the router grounds the turn
in the live directory listing (+ open file, capped) and attaches fs+todo
tools. Live-verified: "which of these files mentions a flashlight, and is
anything misspelled?" answered correctly from a real folder.

### 7. Built-in text/code editor with Lumen assist (todo #17)
Open text, markdown, and code files (py, cpp, html, …) in an editor tab for
manual editing, with a prompt panel that auto-includes the open file + its
directory as context — summarize this file, make this change, write a
section. LLM-proposed edits appear as a preview/diff and apply only on
accept (write-gate, same as item 2). Largest item in this backlog — stage it:
browse (item 6) → ask-about-file → manual editing → assisted edits.

**Built 2026-07-19** (same Files tab). Manual editing: monospace editor,
dirty marker, Save/Ctrl+S (the user's own direct-manipulation write — no
gate); binary/oversized/non-UTF-8 files get honest placeholders. Assisted:
✎ Edit sends the current buffer + instruction to `files.propose_edit`
(`llm/file_edit.py`); the reply leads with a forced `CHANGES:` sentinel line
— without that find-it-first step the 4B reproducibly copied the file
verbatim on "fix the spelling mistake" (live 2026-07-19); with it the same
request fixes flashlite→flashlight and an inapplicable request honestly
proposes nothing. The proposal renders as a colored diff with
Apply / Discard — the preview is the confirmation (compose precedent);
nothing touches disk until Apply. **Deviation from the sketch:** no
write-grant is recorded on Apply — grants stay tied to dialogs that
explicitly promise "future writes without asking".


## 8. Add a way to archive and delete emails

**Built 2026-07-19** (archive already existed end-to-end — reading-pane
button → confirm dialog → Gmail; verified, not rebuilt). New: **Delete**,
implemented as Gmail's **Trash** (recoverable there for ~30 days — the
confirm dialog says so honestly; a permanent wipe was deliberately not
built). `GmailSync.trash` calls the Gmail API then drops the mirror row
(the mirror only holds non-trash mail, matching the bulk pull's
`includeSpamTrash=False`); router `emails.delete` rides the same confirm
gate as archive (generalized into a `MAIL_GATES` table); a Delete button
sits beside Archive in the reading pane. The mail MCP server stays
read-only — delete is a UI-confirmed one-shot, never a model tool.
Live-verified against real Gmail with a self-sent test mail: decline
changed nothing; approve moved it to Trash and the mirror row vanished.


## 9. Look into a way to send text messages, and see messages?

## 10. THE BIG ONE - be able to grab assignments and other information from my u of u canvas account, then add it to the todo, and calendar

---

## Parking lot (named, not committed)
- **Grouped-section list view** (collapsible by label) as an alternative to chips.
- **Snooze** — hide a message until a chosen time.
- **Unsubscribe detection** — surface a one-click unsubscribe for bulk senders.
- **Rule suggestions from history** — "you always label Duke Energy as Bills —
  make it a rule?"
- **Bulk actions** — multi-select rows for archive/label.