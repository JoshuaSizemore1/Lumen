# Canvas content view — card redesign

**Date:** 2026-07-21
**Scope:** `lumen/ui_v3/screens/canvas.py` content view (assignments + announcements),
plus one line in `lumen/daemon/router.py`. No schema, sync, or reconcile changes.

## Problem

The Canvas content view is two flat lists of single-height rows. Everything reads at
one visual weight — an assignment due in 2 hours looks identical to one due in 3 weeks —
and rich stored fields are thrown away: assignments carry `points` and `submitted`,
announcements carry `posted_at` and `message`, none of which the UI renders. Announcements
are headline-only, so the section is a list, not a feed.

## Design

### Assignments → clustered due-date timeline

A single scrollable timeline, globally ordered by due date, with three structure levels:

1. **Time sections** — thin mono eyebrow headers, rendered only when non-empty, in fixed
   order: `OVERDUE` · `THIS WEEK` (today → +6d) · `NEXT WEEK` (+7 → +13d) · `LATER`
   (+14d+) · `NO DUE DATE`. Boundaries are rolling (relative to today), not calendar weeks.
2. **Course clusters inside each section** — walking the due-sorted items, consecutive
   same-course assignments bond into one card; a course change ends the card and starts a
   new one after a padding gap. Clustering is opportunistic on due order, exactly matching
   "two things due before others from the same course are connected."
3. **The cluster card** — `role=panel` QFrame with a course-colored left accent rail
   (`AccentBar`), a header row (course-code chip + course name), then one internal row per
   assignment separated by hairlines.

**Assignment row** (two lines): name (primary) on top with `Open ↗` + `✕` at the right;
meta line below — `points` chip ("100 pt", omitted when null), urgency due chip (existing
`_due_meta`), and submission status: green **"✓ submitted"** when submitted, muted
**"not submitted"** otherwise, red when also overdue.

### Announcements → feed

Vertical feed of cards, newest first. Each card: course-colored accent rail; header row of
course-code chip + relative time (`_ago`: "just now" / "Nm ago" / "Nh ago" / "Nd ago" /
date) with `Add as todo`/`✓ added` + `Open ↗` + `✕` on the right; wrapped title (semibold);
and a 2-line plain-text preview of `message` (HTML stripped, truncated).

### Unchanged

Connect hero, pending "Add N due dates to calendar" button, dismiss-with-undo, per-course
colors, browser stack, auto-login, Manage-courses panel, status pill.

### Build notes

- New helpers in `canvas.py`: `_time_bucket()`, `_ago()`, `_strip_html()`, a `_card()`
  builder (accent rail + content vbox), and chip builders for points/status.
- One daemon line: add `"course_name"` to the `canvas.assignments` payload (router.py).
- No new widgets — `AccentBar`, `Chip`, `ElideLabel`, `label` cover it.
- Verify offscreen via the `verify` skill after building.
