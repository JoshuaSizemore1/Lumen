# Canvas tab fixes — design

Date: 2026-07-21
Scope: three fixes to the Canvas tab. UI in `lumen/ui_v3/screens/canvas.py`;
supporting changes in `state.py`, `router.py`, `canvas_store.py`, `db.py`.

## 1. Make the in-app browser work

Symptoms (reported live by the user; QtWebEngine + Duo only run on his machine):
- Clicking an in-page link "opens the website for a split second then closes it."
- "Browse Canvas" does nothing.

Root causes:
- **Flash-and-vanish links** — Canvas links often use `target="_blank"` /
  `window.open`. `QWebEnginePage` with no `createWindow` override returns a null
  page for new-window requests, so Qt spawns a transient page and discards it.
- **Browse snaps back** — `_apply_status()` calls `_stack.setCurrentIndex(0)`
  on any *connected* status. Every page load replays persisted cookies
  (`loadAllCookies` → `_on_cookie`); a rotated cookie re-hands-off the session →
  `_on_connected` → `_apply_status` → the browser view is yanked back to the
  content list.

Fixes:
- Subclass `QWebEnginePage` and override `createWindow()` to return the same
  page, so new-window links load in the current view instead of a discarded
  popup.
- Separate *login completion* from *browsing*. Add `self._login_mode`. Only
  `_start_login` sets it True. `_open_in_browser(url, login=False)` sets it per
  call. The return-to-content jump moves OUT of `_apply_status` and INTO
  `_on_connected`, gated on `_login_mode` (then the flag is cleared). While
  browsing (`_login_mode` False) a background session refresh updates the status
  pill but does not change the visible page. `_apply_status` no longer touches
  the stack.
- Downloads: connect `QWebEngineProfile.downloadRequested` to a handler that
  sets the download directory to `~/Downloads` and accepts — so clicking a
  Canvas file saves it instead of silently doing nothing.

## 2. Dismiss assignments & announcements (gone, with undo)

- New `dismissed INTEGER NOT NULL DEFAULT 0` column on `canvas_assignments` and
  `canvas_announcements` (schema + additive migration in `db.py`). UPSERTs keep
  the flag (they never touch it), so a dismissal survives future syncs.
- `CanvasStore`: `set_assignment_dismissed(id, bool)` and
  `set_announcement_dismissed(id, bool)`. Display queries filter `dismissed = 0`:
  `active_assignments()` (feeds the tab list AND the pending-calendar count, so
  they declutter together) and `announcements()`. Reconciliation is left alone —
  dismiss hides the item; it does not delete an already-linked todo or calendar
  marker.
- Router: `canvas.dismiss_assignment` and `canvas.dismiss_announcement`, each
  `{id, dismissed}` → the matching store setter → `{result: {ok: true}}`
  (mirrors `canvas.set_course_included`).
- State seam (`ui_v2/state.py`): `canvas_dismiss_assignment(id, dismissed, cb)`
  and `canvas_dismiss_announcement(id, dismissed, cb)`.
- UI: a trailing **✕** icon on each assignment/announcement row. Clicking it
  calls the daemon (dismissed=1), refreshes the list (item vanishes), and shows
  a transient undo bar at the top of the content view:
  "'{name}' dismissed — Undo". A `QTimer` (~6s) clears the bar; Undo calls the
  daemon (dismissed=0) and refreshes. `self._last_dismissed = (kind, id)` records
  what to reverse.

## 3. Auto-open Connect on tab show when disconnected

- `showEvent` arms `self._auto_login_armed = True`. In `_apply_status`, once the
  daemon reports **not connected** and the stack is on the content page, disarm
  and call `_start_login()` (which sets `_login_mode` and autofills saved
  credentials). Connected → never fires. Arming only from `showEvent` keeps a
  Disconnect click from immediately relaunching the login.

## Out of scope
Sync/poller, reconciliation logic, the MCP layer, and the daemon-side Canvas
client are untouched.
