# Phase 7 (part 2) — Writing style: Design

Date: 2026-07-13. Completes Phase 7 on top of the compose/send slice
(`2026-07-13-phase7-email-compose-design.md`). Derive-once/apply-often per
`writing-style.md`: a strong model derives a small rules file from the sent-mail
corpus once; the fast model applies it on every draft/revise.

Design gate: the one product/privacy question the dev plan reserves for the user
— which model reads the sent mail — was asked and answered 2026-07-13: **this
Claude Code session** (best quality; sent mail transits Anthropic's API once),
over the local qwen3:14b (private, weaker) and a paid API key (re-runnable).

## User-visible behavior

- Email drafts and revisions (chat-driven compose, Mail-screen popup, the
  in-popup Revise box) now come out in Josh's own voice — greeting, sign-off,
  phrasing, register — instead of generic assistant prose.
- The style lives in one hand-editable file:
  `~/.local/share/lumen/writing-style.md`. Editing a line changes the very next
  draft (read per call, grants-file convention — no restart, no cache).
  Deleting the file cleanly reverts drafting to unstyled.
- Refresh is on request (ask Claude Code to re-derive; quarterly cadence per
  `writing-style.md`), never a background job.
- Nothing about sending changed: the editable popup remains the confirmation;
  the style rules only shape the pre-filled text.

## Derivation (performed in-session, 2026-07-13)

- Corpus: the Phase 6 mirror, `labels LIKE '%,SENT,%'` — 47 rows, of which 15
  were genuinely hand-written (Gmail auto-generated unsubscribe mails, empty
  bodies, and Lumen's own Phase 7 test send excluded; quoted reply/forward
  tails stripped).
- Output follows the `writing-style.md` template: two registers
  (professional/casual), greeting + sign-off patterns, phrasing quirks,
  avoid-list. Prefaced with derivation date, corpus note, and the refresh
  recipe so the file is self-describing for hand-editors.

## Architecture

- `daemon/config.py::default_style_rules_path()` — XDG data dir, beside the
  grants file.
- `daemon/llm/writing_style.py` — `load_rules()` (per-call read, `None` when
  missing/empty, capped at `MAX_CHARS = 2500` so the file can't blow the 4B
  prompt budget) and `styled(system)` (appends the rules plus a "the user's
  explicit request wins over these rules" line — revision instructions like
  "more formal" must override the file).
- `daemon/llm/email_compose.py` — `propose_email` and `revise_email` wrap
  their system prompts in `writing_style.styled(...)`. No router, UI, or IPC
  changes; the style is invisible plumbing behind the existing compose flow.

## Testing

- Unit: rules load/cap/missing-file; `styled` append + pass-through; style
  block riding both draft and revise prompts, absent when no file (suite 500
  green).
- Live (real daemon + real Ollama over the socket): a compose-shaped chat
  produced a draft opening "Hello Vivian,", using "I was wanting to…" /
  "let me know what works with your schedule", closing "I look forward to
  hearing from you." / "Respectfully,\nJoshua Sizemore" — and cancel sent
  nothing. First run exposed a filler opener ("I hope you're doing well");
  sharpening the file's avoid-list fixed it on the next run, proving the
  hand-edit loop. Residual: the 4B model occasionally slips a rule (one em
  dash observed) — acceptable, drafts are editable by design.

## Out of scope

Styling non-email prose (chat answers, briefings); automatic refresh triggers;
per-recipient style profiles; shipping derivation code (the recipe lives in
`writing-style.md` — extraction is a one-off query, synthesis needs the strong
model, so there is nothing worth maintaining in-repo).
