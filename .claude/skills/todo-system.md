# Skill: Todo System

## Storage
Local SQLite table (`db.py` bootstraps it at daemon startup; file at
`[storage] db_path`, default `$XDG_DATA_HOME/lumen/lumen.db`):
```sql
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_date TEXT,                          -- ISO local date, nullable
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,               -- ISO timestamp
    source TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'llm-extracted' | 'email' | 'calendar'
    tags TEXT NOT NULL DEFAULT '[]'         -- JSON array of lowercase strings
);
```
Todos can carry several tags (decided 2026-07-08): a JSON array column, not a
join table — tags come back with the row, `json_each` covers future filtering,
hand-written schema change if tags ever become a first-class query dimension.

## Input grammar (@date / #tag)
`parse_todo_input(raw, today)` in `daemon/connectors/todo_parse.py` —
deterministic, daemon-side (the UI ships raw text), shared by manual adds now
and NL capture later.
- `@today`, `@tomorrow`, `@fri`/`@friday` (nearest occurrence, today counts),
  `@2026-07-12`, `@07-12`, `@jul9` (year-less forms roll to next year if
  already past). Several `@` dates: last one wins. Unrecognized `@token`
  stays in the text.
- `#tag`: each `#word` token becomes a tag — lowercased, deduped, order kept.
- Matched tokens are stripped from the stored text.

## Natural-language capture
- Quick-launcher accepts free text; router detects intent (add todo vs question vs command) via cheap heuristic first, LLM fallback if ambiguous.
- LLM can extract implied todos from email/calendar content ("reminds you to follow up with X") but always inserts them as `source = 'llm-extracted'` and surfaces them for confirmation before they're treated as a committed todo — don't silently add tasks the user didn't explicitly ask for.

## What the LLM should be able to do
- "What do I have due today/this week"
- "Add a todo: X"
- "Mark X done"
- Suggest todos from scanned email/calendar content, clearly flagged as suggestions

Status update (Phase 8, 2026-07-13): NL add / mark-done / quick capture are LIVE — see `daily-features.md` "Quick capture" for the as-built (capture.py classifier, `capture_ok` flag, TODO_ADD/MARK_DONE routes). LLM-extracted suggestions land with commitment tracking (feature 3).

Status (Phase 2, 2026-07-08): read queries are live — "what's due today/this
week" via router context injection behind a keyword heuristic (`todo(s)`,
`task(s)`, `due`, `overdue`). NL add / mark-done / suggestions are not built;
they land with Phase 8 quick capture and the MCP phases.

## What NOT to do
- Don't build a priority/scoring algorithm unless asked — a flat list sorted by due date is enough for v1.
- Don't silently promote LLM-extracted suggestions into real todos without confirmation.
