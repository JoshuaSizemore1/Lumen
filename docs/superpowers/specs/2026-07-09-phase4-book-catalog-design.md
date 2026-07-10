# Phase 4 — Book Catalog + Recommendations: Design

Date: 2026-07-09. Builds on `.claude/skills/book-catalog.md` (storage + grounding rules),
the `BooksScreen` mockup in `lumen/ui/book_catalog.py`, and the Phase 3 MCP plumbing
(Open Library server `lumen/mcp_servers/openlibrary.py`, tool loop, tool log).

## User-visible behavior

**Reading log (left side of the Books screen).** An add-entry form at the top: Title
(required), Author, star rating 1–5 (click stars, optional), Notes (optional, free
text). Pressing "Log" saves the entry with `date_finished` stamped as today — you log
a book when you finish it. (The column stays nullable in the schema; a date field for
back-filling old reads is a possible later addition, not in the v1 form.) Entries list
newest-first: title, author, stars, finished date, notes.
Delete works like the todo screen (left-click the delete affordance on a row). No edit
in v1 — delete and re-add covers the rare typo.

**Suggested next (right panel, dashed purple).** Shows the *last generated* set of up
to 3 suggestions — title, author, one-line rationale tied to specific catalog entries —
plus a "generated <date>" footer. A **Suggest next** button regenerates on demand.
Nothing generates automatically: no model load or network lookup happens unless the
user asks (button or chat). Decided 2026-07-09: button + chat entry points, cached
panel, no auto-refresh — keeps the thermal cost user-initiated.

Each suggestion has "**+ add to log**", which **pre-fills the add form** with
title/author — it never inserts directly, because the log means "books I've read" and
an entry needs the user's own rating/notes/finish date. This satisfies
book-catalog.md's "don't auto-add recommendations."

**Chat.** "What should I read next?" runs the same recommendation pipeline and returns
the same grounded set as chat text (and refreshes the panel cache). Catalog questions
("have I read X", "what did I rate Y") get the catalog injected as context, mirroring
the todo-context pattern.

**Offline/errors.** Daemon unreachable → same offline banner pattern as the todo
screen, form disabled. Ollama unavailable → error message, panel keeps its cached set.
Open Library unreachable or nothing verifiable returned → honest failure ("couldn't
get grounded suggestions right now"), cached set preserved. Empty catalog → "log a few
books first" instead of ungrounded guessing.

## Grounding guarantee (the critical constraint)

The model must never present a book it invented. Enforcement is mechanical, not
prompt-only:

1. The recommendation flow collects every `search_books`/`get_book` **result returned
   during that request** (the executor already sees each result; the rec flow keeps
   them in memory in addition to the JSONL tool log).
2. The model is prompted to output suggestions in a fixed line format
   (`Title — Author — rationale`), parsed leniently.
3. The daemon **drops any suggestion whose title doesn't appear in the collected tool
   results** (case-insensitive match), and drops anything already in the catalog
   (case-insensitive title match).
4. Only survivors are cached, shown, and spoken. Zero survivors → the honest-failure
   message above.

This makes the Phase 4 success criterion checkable: every displayed suggestion
provably traces to a logged tool result in `tool-calls.jsonl`.

## Data

Two tables added to `daemon/db.py`'s hand-written schema (no migration framework):

```sql
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    date_finished TEXT,              -- ISO date, nullable
    rating INTEGER,                  -- 1-5, nullable
    notes TEXT,                      -- nullable
    tags TEXT NOT NULL DEFAULT '[]', -- JSON array of lowercase strings
    created_at TEXT NOT NULL         -- ISO timestamp
);
CREATE TABLE IF NOT EXISTS book_recs (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    rationale TEXT,
    generated_at TEXT NOT NULL       -- ISO timestamp, same for the whole set
);
```

Deviation from book-catalog.md: `tags` is a JSON array (matching the todos
convention), not comma-separated — update the skill file when implementing. Tags have
no form field in v1; the LLM derives themes from ratings/notes. `book_recs` holds only
the latest set (delete-all + insert on refresh).

## Architecture

- **`daemon/connectors/books.py` — `BookStore`**: `add(title, author, rating, notes,
  date_finished)`, `list_all()` (newest first), `delete(id)`, mutations return the
  fresh list (todos convention); `catalog_context()` renders the catalog for system-
  message injection (explicit empty marker, like `todo_context`); recs cache accessors
  `save_recs(set)` / `latest_recs()`.
- **Recommendation pipeline** (one pipeline, two entry points): inject
  `catalog_context()` as system context — the catalog is local data, not worth a tool
  round-trip on a small model — then run the Phase 3 tool loop restricted to the
  `books` server's tools, collect tool results, parse + validate per the grounding
  rules, save survivors to `book_recs`, return the structured set. Chat renders the
  validated set as text (non-streamed, like all Phase 3 tool turns); the one-shot
  returns it as a result payload.
- **Router additions**: one-shots `books.list`, `books.add`, `books.delete`,
  `books.recommend` (runs pipeline), `books.recs` (cached set, no LLM). Chat: a
  recommendation-intent hint (e.g. read/recommend + book vocabulary) routes to the
  pipeline before the generic `TOOL_HINT` path; a `BOOK_HINT` triggers catalog-context
  injection the way `TODO_HINT` does.
- **Model routing**: fast-path model — Phase 3 verified 1–2 tool calls on
  `qwen3:4b-instruct`; the `ModelRouter` seam already exists if quality forces
  escalation later. No change now.
- **UI (`ui/book_catalog.py`)**: replace hardcoded `BOOKS`/`RECS` with live wiring via
  `daemon_client` (todo-manager pattern): load on open (`books.list` + `books.recs` —
  both DB-only, no LLM), form submit → `books.add`, row delete → `books.delete`,
  Suggest next → `books.recommend` with a busy state while it runs.

## Testing

- `BookStore` CRUD + recs cache on in-memory SQLite (todos-test pattern).
- Grounding validation as pure unit tests: fake tool transcripts → invented titles
  dropped, catalog titles deduped, zero-survivors → failure signal; lenient parser
  cases (extra prose, missing author).
- Router: `books.*` one-shots, rec-hint routing precedence over `TOOL_HINT`,
  `BOOK_HINT` context injection.
- UI: screen renders from daemon data, submit/delete round-trips, rec panel busy +
  cached states (test_todo_manager/test_screens patterns).
- Live verification (phase gate, manual): real books logged, real recommendation run
  against Ollama + Open Library; confirm each shown suggestion appears in
  `tool-calls.jsonl` results — the development-plan success criterion.

## Out of scope (Phase 4)

- Editing existing entries; tags form field; rec history beyond the latest set;
  auto-adding recommendations; Google Books or any second lookup source.
