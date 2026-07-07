# Skill: Todo System

## Storage
Local SQLite table, minimal schema:
```sql
CREATE TABLE todos (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_date TEXT,          -- ISO date, nullable
    completed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    source TEXT             -- 'manual' | 'llm-extracted' | 'email' | 'calendar'
);
```

## Natural-language capture
- Quick-launcher accepts free text; router detects intent (add todo vs question vs command) via cheap heuristic first, LLM fallback if ambiguous.
- LLM can extract implied todos from email/calendar content ("reminds you to follow up with X") but always inserts them as `source = 'llm-extracted'` and surfaces them for confirmation before they're treated as a committed todo — don't silently add tasks the user didn't explicitly ask for.

## What the LLM should be able to do
- "What do I have due today/this week"
- "Add a todo: X"
- "Mark X done"
- Suggest todos from scanned email/calendar content, clearly flagged as suggestions

## What NOT to do
- Don't build a priority/scoring algorithm unless asked — a flat list sorted by due date is enough for v1.
- Don't silently promote LLM-extracted suggestions into real todos without confirmation.
