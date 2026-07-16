# Lumen — Inbox feature ideas

Living backlog + design of record for the Mail screen. First section is the
committed design for the current batch; the parking lot at the bottom holds
ideas we've named but are **not** building yet.

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

## Parking lot (named, not committed)
- **Grouped-section list view** (collapsible by label) as an alternative to chips.
- **Snooze** — hide a message until a chosen time.
- **Unsubscribe detection** — surface a one-click unsubscribe for bulk senders.
- **Rule suggestions from history** — "you always label Duke Energy as Bills —
  make it a rule?"
- **Bulk actions** — multi-select rows for archive/label.
