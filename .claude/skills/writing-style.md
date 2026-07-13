# Skill: Writing Style Ruleset

## Why this is derive-once, apply-often
Extracting a coherent style guide from a corpus of sent emails is a synthesis task that benefits from a strong model — small local models tend to produce generic or shallow results here. But applying an already-written style guide to draft a new email is easy, well within a small model's ability. So: derive rarely with the strongest model you're willing to use, apply constantly with the small default model.

## Derivation options (pick one)
1. **Cloud (Claude)** — best quality, requires being fine with your sent-email corpus going through the API once. Fastest path to a good result.
2. **Local escalation-tier model** (see `mcp-integration.md`) — fully private, lower quality than (1), run during idle time since it's a heavier one-off job, not something needing to be fast.

## Output format
A plain text/markdown rules file (not a model, not a fine-tune) — something like:
```
- Opens with [pattern], rarely uses [X phrase]
- Sentence length tends to be [short/long], [formal/casual] tone
- Sign-off pattern: [...]
- Avoids: [...]
- Common phrasing quirks: [...]
```
This gets loaded as context whenever the local model drafts an email — small, static, cheap to include.

## Refresh cadence
Occasional, not continuous — quarterly is a reasonable default, or triggered manually if your writing style shifts noticeably. Not a background job like the memory system; this doesn't need to react to every new sent email.

## As built (Phase 7, 2026-07-13)
- **Route chosen by the user: cloud via the Claude Code session itself** — no
  API key, no derivation code shipped. Claude read the corpus and wrote the
  file directly. Refresh = ask Claude Code to re-derive (recipe below).
- **Rules file**: `~/.local/share/lumen/writing-style.md`
  (`config.default_style_rules_path()`, XDG data dir). Self-describing header
  (derivation date, corpus note, refresh recipe). Hand-editing it is the
  correction interface — live-verified: adding one avoid-list line changed the
  next draft.
- **Application**: `daemon/llm/writing_style.py` — `load_rules()` reads per
  call (grants-file convention: edits apply immediately, delete reverts to
  unstyled) capped at `MAX_CHARS = 2500`; `styled(system)` appends the rules
  plus "the user's explicit request wins over these rules" (so a revise
  instruction like "more formal" overrides the file). Both `propose_email`
  and `revise_email` in `email_compose.py` wrap their system prompts in it.
  Nothing else changed — no router/UI/IPC surface.
- **Refresh recipe** (what Claude should redo on request/quarterly): query the
  mirror for `labels LIKE '%,SENT,%'`; drop auto-generated mail (Gmail
  unsubscribe sends, <5-word bodies) and strip quoted tails (`On … wrote:`,
  `> ` lines, forwarded blocks); synthesize per the template above; overwrite
  the file, preserving any hand edits worth keeping (diff it first).
- **Adherence reality check**: qwen3:4b-instruct follows the file well but
  not perfectly (an occasional em dash slips through). Acceptable — the
  compose popup is editable and drafts are drafts.

## What NOT to do
- Don't ask the small default local model to derive this from scratch — expect weak results.
- Don't rebuild it on every draft — it's a static reference file, refreshed occasionally.
- Don't skip explicit confirmation before actually sending anything drafted using this — same write-confirmation rule as any other email action.
