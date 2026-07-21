# Canvas UI rework — design (issue #28)

Date: 2026-07-21
Status: design, pending implementation
Supersedes nothing; extends the Canvas integration (`2026-07-20-canvas-integration-design.md`)
and the #27 adaptive-header redesign.

## Goal

Josh's issue #28, three asks:

1. A button to view the Canvas website and browse it like a normal website.
2. Each assignment should have a button/link to open it in Canvas.
3. Turn courses on/off so information from past classes is not pulled in.

Decisions taken during brainstorming:

- **Browse model:** summary-first, in-app browser on demand (preserves the
  zero-idle-Chromium power constraint — Chromium only works while the user is
  actively on the browser view).
- **Open target:** assignments/announcements open in Lumen's own in-app Canvas
  browser (reuses the persisted session — no re-login).
- **Course control:** a dedicated "Manage courses" panel with on/off toggles;
  no per-course filtering of the summary.
- **Off behavior:** switching a course off removes its stuff too — hide it from
  the summary, stop pulling it, AND remove the todos + calendar entries it
  created. Because deleting Google Calendar events is an external write, the
  toggle-off surfaces a confirmation before anything is deleted (project rule:
  writes are confirmed in the UI).

## Non-goals

- No live/always-on embedded browser (rejected: constant Chromium = battery/heat).
- No per-course filtering of the assignment/announcement lists.
- No change to how the session is captured or handed to the daemon — the
  existing cookie hand-off (`_on_cookie` → `canvas.set_session`) is reused.

---

## A. Course on/off

### Data model

`canvas_courses` gains a user-preference column, kept distinct from the
automatic enrollment signal:

- `active` (existing) — 1 when Canvas reports the course in the live active
  enrollment set. Auto-managed by `deactivate_courses_except`. **Unchanged.**
- `included` (new, `INTEGER NOT NULL DEFAULT 1`) — the *user's* switch. New
  courses default on, so a genuinely new enrollment appears automatically.

Migration: additive `ALTER TABLE canvas_courses ADD COLUMN included ...` in
`db.py`, following the existing `marker_due` migration pattern. Guard with a
`PRAGMA table_info` check so it is idempotent.

A course is **pulled and shown** only when `active = 1 AND included = 1`.

### Store (`canvas_store.py`)

- `set_course_included(course_id, included: bool)` — set the flag.
- `courses_for_panel()` → all `active = 1` courses with their `included` flag,
  ordered for display (code/name). Feeds the Manage panel.
- `pull_course_ids()` → `[id]` where `active = 1 AND included = 1`. The sync
  fetch set.
- `active_assignments()` / `active_courses()` / `assignments()`-for-summary and
  `courses_by_id()` become scoped to `active = 1 AND included = 1`, so excluded
  courses vanish from the summary and from reconciliation. (Kept as the same
  method names; the JOIN/WHERE gains `AND included = 1`.)
- `links_for_course(course_id)` → assignments for that course that carry a
  `todo_id` or `calendar_event_id` (for the purge preview + execution).
- `clear_course_reconciliation(course_id)` → null out `todo_id`,
  `calendar_event_id`, `marker_due`, `first_seen` and leave `handled = 0` for
  that course's assignments, so re-enabling later reconciles fresh.

### Sync (`canvas_sync.py`) — two-phase fetch

Today `_fetch_blocking` pulls assignments/announcements for **every** active
course. To honor "do not pull information from past classes", split the fetch so
excluded courses cost zero network:

1. Worker thread: fetch the course list from Canvas.
2. Loop thread: `upsert_courses` + `deactivate_courses_except`, then read
   `pull_course_ids()` (this respects existing `included` flags; brand-new
   courses default `included = 1` via the upsert, so they are pulled).
3. Worker thread: fetch assignments + announcements **only** for
   `pull_course_ids()`.
4. Loop thread: upsert those, reconcile, flag announcements (unchanged).

All SQLite stays on the loop thread; all network stays on worker threads — the
existing threading discipline is preserved, just reordered.

### Toggle-off purge (write-confirmation flow)

New IPC route `canvas.set_course_included` with `{course_id, included}`:

- **Turning ON:** set `included = 1`, return status. The next poll (or an
  immediate `sync_once`) re-fetches and reconciles the course from scratch;
  calendar markers are re-offered through the normal `pending_calendar` path.
- **Turning OFF:**
  - If the course has no linked todos and no calendar markers
    (`links_for_course` empty): apply immediately — `set_course_included(0)`,
    then trigger a re-sync so the summary drops it. No confirmation needed
    (nothing destructive).
  - If it has links: yield a **confirmation** (mirroring the existing
    `canvas_due_dates` / `_gated_*` pattern) describing the removal, e.g.
    *"Turn off CS 3500 — this removes 3 todos and 2 calendar events it created."*
    On accept: delete the linked calendar events (the confirmed external write,
    via the same delete path `calendar.delete` uses), delete the linked todos
    (`todos.delete` — local, covered by the same accept), then
    `set_course_included(0)` + `clear_course_reconciliation(course_id)`, and
    re-sync. On decline: nothing changes, the toggle snaps back to on.

`canvas.courses` (new read route) returns `courses_for_panel()` for the UI.

### Manage courses panel (UI)

A "Manage courses" button in the Canvas header (connected state only) opens the
panel. Options considered for surfacing it: inline expanding section vs modal
overlay. **Choose an in-tab overlay** consistent with the app's existing overlay
pattern (compose/edit dialogs), so it dims the summary and is dismissible.

Panel contents:

- Title "Courses" + a one-line hint: "Turn off classes you're done with — Lumen
  stops pulling them."
- One row per `active` course: course-code chip (same `T.label_color` as the
  summary), full name, and a toggle (`QCheckBox` styled as a switch, matching
  existing settings toggles).
- Flipping a toggle calls `state.canvas_set_course_included`; an off-flip that
  needs confirmation shows the confirm text inline/below that row (or the app's
  standard confirm surface) before committing.
- Closing the panel refreshes the summary (`_refresh_content` + status), so the
  change is visible immediately.

---

## B. In-app Canvas browser

Unify the login view and a general browser into one on-demand browser view in
the existing `QStackedWidget` (index 1, where the login view already lives).

### Browser view

The lazy `QWebEngineView` + persistent `QWebEngineProfile` are unchanged
(built only on first use → headless-safe, zero idle cost). Wrap the web view in
a slim toolbar:

```
┌ ‹  ›  ⟳   <page title / host>                       [ Done ] ┐
│                                                              │
│              ( QWebEngineView — live Canvas )                │
└──────────────────────────────────────────────────────────────┘
```

- **‹ / ›** — `web.back()` / `web.forward()`, enabled/disabled from
  `web.history().canGoBack()/canGoForward()` (update on `urlChanged`).
- **⟳** — `web.reload()`.
- **title/host** — an `ElideLabel` bound to `web.titleChanged` (fallback to the
  URL host).
- **Done** — return the stack to the summary (index 0). The web view is left
  built but idle; Chromium does no work off-screen.

### Entry points

All three build/reveal the same browser view (lazy-construct on first call):

- **Header button** — adaptive, reusing the #27 header logic:
  - Disconnected → **"Connect Canvas"** → browser to `<base_url>/login`.
  - Connected → **"Browse Canvas"** → browser to `<base_url>` (dashboard).
- **Assignment "Open ↗"** → browser to the assignment's `html_url` (§C).
- **Announcement "Open ↗"** → browser to the announcement's `html_url`.

Because Connect and Browse are the same view, an expired session simply lands on
Canvas's own login page (saved credentials autofill via the existing
`autofill_js`), and a successful login hands the session to the daemon through
the existing `cookieAdded` → `canvas_set_session` path. No separate login code
path remains.

---

## C. Open assignment / announcement in Canvas

- `canvas_assignments` already stores `html_url`; `canvas_announcements` too.
  No new sync or data needed.
- Assignment row (in `_render_assignments`) gains a trailing **"Open ↗"** button
  (`soft`, small, matching the existing `_cal_btn` sizing) that calls
  `self._open_in_browser(a["html_url"])`. Rows with no `html_url` omit the
  button.
- Announcement row (in `_render_announcements`) gains the same **"Open ↗"**,
  alongside the existing "Add as todo" / "✓ added" affordance.
- `_open_in_browser(url)` builds the web view if needed, navigates, and shows
  the browser view.

---

## State / IPC seam

New methods on `ui_v2/state.py` `AppState` (the shared seam; `ui_v3` inherits):

- `canvas_courses(cb)` → `canvas.courses`.
- `canvas_set_course_included(course_id, included, cb)` → `canvas.set_course_included`.

Both go through the generic `self._data.request(...)`. Sample-mode fallbacks
return a small fixture list so the panel renders offline like the rest of the UI.

Daemon router: add `canvas.courses` (read) and `canvas.set_course_included`
(read + gated write on off-with-links), reusing `_confirm` / the calendar-delete
path already used by `canvas.push_due_dates`.

---

## Adaptive header — final states (builds on #27)

- **Disconnected:** welcome hero + "Connect Canvas" + "Remember my login".
  (Manage courses hidden.)
- **Connected:** status pill + "Browse Canvas" + "Manage courses" + "Disconnect".
  Assignment/announcement rows show "Open ↗".
- **Browsing:** summary swaps for the browser view; "Done" returns.

---

## Testing

Headless-friendly, matching the existing suite (the web view stays lazy so
nothing constructs Chromium during tests):

- `tests/daemon/connectors/test_canvas_store.py`: `included` flag round-trip;
  `pull_course_ids` / scoped `active_assignments` exclude an off course;
  `links_for_course` + `clear_course_reconciliation`.
- `tests/daemon/connectors/test_canvas_sync.py`: an excluded course is **not**
  fetched (assert the injected client's `assignments`/`announcements` are never
  called for it); a re-enabled course is fetched again.
- `tests/daemon/test_router.py` (or a canvas-router test): `canvas.courses`
  shape; `canvas.set_course_included` off-with-links yields a confirmation and,
  on accept, issues the calendar deletes + todo deletes; off-without-links
  applies directly; on returns status.
- `tests/ui/test_canvas_screen.py`: "Open ↗" present per assignment/announcement
  and calls `_open_in_browser` with the right `html_url`; Manage panel lists
  courses with toggles and calls `canvas_set_course_included`; the browser
  toolbar's Done returns the stack to index 0; header button text/target adapts
  by connection state. Screenshot the summary (Open buttons) and the Manage
  panel.

## Files touched

- `lumen/daemon/db.py` — `included` column + migration.
- `lumen/daemon/connectors/canvas_store.py` — flag + scoped reads + purge helpers.
- `lumen/daemon/connectors/canvas_sync.py` — two-phase gated fetch.
- `lumen/daemon/connectors/canvas_reconcile.py` — inherits scoped
  `active_assignments` (verify no direct unscoped reads).
- `lumen/daemon/router.py` — `canvas.courses`, `canvas.set_course_included`.
- `lumen/ui_v2/state.py` — two new seam methods + sample fallbacks.
- `lumen/ui_v3/screens/canvas.py` — browser toolbar + entry points, Open
  buttons, Manage courses panel, adaptive header button.
- Tests as above.
