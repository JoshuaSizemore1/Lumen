# Skill: Book Catalog & Recommendations

## Storage
Local SQLite table:
```sql
CREATE TABLE books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    date_finished TEXT,     -- ISO date, nullable (may not remember exactly)
    rating INTEGER,         -- 1-5, nullable
    notes TEXT,             -- free-text impressions, nullable
    tags TEXT               -- comma-separated genre/theme tags, nullable
);
```
Entries are added manually (typed in, no external write actions involved) — no confirmation flow needed here, this is just personal cataloging.

## Recommendation flow — must be lookup-grounded, not memory-grounded
This is the critical constraint: the LLM must never recommend a book from its own memory alone. Local models hallucinate titles, authors, and plot details, and a confidently wrong recommendation is worse than none.

Correct flow:
1. LLM reads your catalog (titles, authors, ratings, tags, notes) and derives search intent — genres, authors, themes you rated highly, patterns across notes.
2. LLM issues search queries to a books lookup source (see below) — NOT its own training data.
3. LLM filters/dedupes returned results against your existing catalog (don't recommend something you've already logged).
4. LLM presents only books that came back from the actual lookup, with a short rationale tied to specific things in your catalog ("you rated X and Y highly, both are Z genre — this shares that").

## Lookup source options
- **Open Library API** — free, no API key required, good coverage, straightforward REST API. Simplest to wire up directly or via a lightweight MCP wrapper.
- **Google Books API** — free tier, API key required, broader metadata (descriptions, similar-books signals in some cases).
- A dedicated books MCP server if one with good maintenance exists at build time — otherwise a thin custom MCP wrapper around Open Library is little effort and keeps the "route everything through MCP" pattern consistent with `mcp-integration.md`.

## What NOT to do
- Never let the LLM state a book exists, describe its plot, or attribute an author without that book coming from an actual lookup result in the current request.
- Don't auto-add recommended books to the catalog — recommendations are suggestions to read, not entries you've read.
