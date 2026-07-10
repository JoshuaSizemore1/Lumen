# Skill: Book Catalog & Recommendations

## Storage
Local SQLite tables (implemented in `daemon/db.py`):
```sql
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    date_finished TEXT,                     -- ISO date, nullable
    rating INTEGER,                         -- 1-5, nullable
    notes TEXT,                             -- nullable
    tags TEXT NOT NULL DEFAULT '[]',        -- JSON array of lowercase strings
    created_at TEXT NOT NULL                -- ISO timestamp
);
CREATE TABLE IF NOT EXISTS book_recs (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    rationale TEXT,
    generated_at TEXT NOT NULL              -- ISO timestamp, same for the whole set
);
```
Entries are added manually (typed in, no external write actions involved) — no confirmation flow needed here, this is just personal cataloging.

Implementation notes (2026-07-10, Phase 4): `tags` is a JSON array (matching the
todos convention), not comma-separated; no tags field in the add form yet — the LLM
derives themes from ratings/notes. `date_finished` is stamped "today" on add.
`book_recs` caches only the latest suggestion set. Full design:
`docs/superpowers/specs/2026-07-09-phase4-book-catalog-design.md`.

## Recommendation flow — must be lookup-grounded, not memory-grounded
This is the critical constraint: the LLM must never recommend a book from its own memory alone. Local models hallucinate titles, authors, and plot details, and a confidently wrong recommendation is worse than none.

Correct flow:
1. LLM reads your catalog (titles, authors, ratings, tags, notes) and derives search intent — genres, authors, themes you rated highly, patterns across notes.
2. LLM issues search queries to a books lookup source (see below) — NOT its own training data.
3. LLM filters/dedupes returned results against your existing catalog (don't recommend something you've already logged).
4. LLM presents only books that came back from the actual lookup, with a short rationale tied to specific things in your catalog ("you rated X and Y highly, both are Z genre — this shares that").

Enforcement is mechanical, not prompt-only: the daemon drops any suggestion whose
title doesn't appear in a tool result returned during the same request
(`daemon/llm/book_recs.py::validate_recs`), so an invented book can't reach the UI.
Recommendations generate only on demand (Suggest next button, or a rec-intent chat
message) and the latest set is cached for display — no auto-refresh (thermal rule).

## Lookup source options
- **Open Library API** — free, no API key required, good coverage, straightforward REST API. Simplest to wire up directly or via a lightweight MCP wrapper.
- **Google Books API** — free tier, API key required, broader metadata (descriptions, similar-books signals in some cases).
- A dedicated books MCP server if one with good maintenance exists at build time — otherwise a thin custom MCP wrapper around Open Library is little effort and keeps the "route everything through MCP" pattern consistent with `mcp-integration.md`.

## What NOT to do
- Never let the LLM state a book exists, describe its plot, or attribute an author without that book coming from an actual lookup result in the current request.
- Don't auto-add recommended books to the catalog — recommendations are suggestions to read, not entries you've read.
