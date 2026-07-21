# App-wide swipe back/forward + adaptive "Add X" — design

Date: 2026-07-21
Reworks two earlier fixes (todo-fixes #18 and #19) into what Josh actually
wanted:

- **#18** was scoped to the calendar only and navigated mid-swipe. Josh wants an
  **application-wide** browser-style gesture: a small circle with an arrow
  drags in as you swipe, and navigation commits only on **release**.
- **#19** made a bare "Add X" always default to todos. Josh wants it
  **adaptive to the page**: on the Calendar page it adds a calendar event; on
  every other page it adds a todo.

Both are behavior reworks in `ui_v3` (plus one router change for #19). No
daemon/model/thermal implications.

---

## Part A — App-wide back/forward swipe (#18)

### Behavior (agreed)

- Two-finger horizontal swipe anywhere in the app.
- As the user drags, a ~40px circle **slides in from the edge and grows with
  the swipe** — it does NOT navigate yet.
- Navigation commits only when the gesture **ends (fingers lifted)** and the
  accumulated swipe passed a threshold. Release early → the circle eases back
  out, no navigation.
- Direction (arrow points opposite the swipe, per Josh):
  - **Swipe right** → circle from the **left** edge, **‹** (left) arrow →
    release → **back**.
  - **Swipe left** → circle from the **right** edge, **›** (right) arrow →
    release → **forward**.
  - Right = back is the existing codebase convention; kept. One-line flip if
    needed.
- **End-of-history bounce:** if there is no entry in that direction, the circle
  still appears but is capped/dimmed and never commits ("can't go" feel).

### History model (agreed: unified, browser-style)

One global history for the whole app. Every location the app settles on is
recorded in visit order, whether the change was a **section switch**
(Mail→Calendar) or an **in-page step** (opened a day, changed the calendar
view). Back walks the list in reverse one step per swipe; forward re-walks it.
Landing on a new location after going back **discards the forward tail**.

### Components (4 isolated units + wiring)

1. **`NavController`** — new, `lumen/ui_v3/nav_history.py`. Pure history model,
   **no Qt** (unit-testable in isolation).
   - State: `history: list[NavEntry]`, `index: int`.
   - `NavEntry = (screen_key: str, token: object | None)` — `token` is opaque to
     the controller; only the owning screen interprets it.
   - `visit(entry)`: if `entry` equals the current entry, no-op (dedup
     consecutive duplicates); else drop the forward tail
     (`history[index+1:]`), append, `index = len-1`.
   - `can_back() / can_forward()` → bool.
   - `back() / forward()` → the `NavEntry` to restore, or `None` at the end.
     These move the cursor but never call `visit`.
   - Optional history cap (e.g. 100 entries) trimmed from the front.

2. **`SwipeNavigator`** — new, `lumen/ui_v3/swipe_nav.py`. A `QObject` event
   filter installed on the content area (the widget hosting the `QStackedWidget`).
   - Watches `QEvent.Type.Wheel`. Acts only on **horizontal-dominant** events
     (`abs(dx) > abs(dy)`); everything else is passed through untouched so
     vertical scrolling inside screens is unaffected.
   - Commit-on-release state machine, using Qt scroll **phases**:
     - `ScrollBegin` (or first horizontal wheel when phase is `NoScrollPhase`)
       → begin gesture, `accum = 0`.
     - `ScrollUpdate` → accumulate `pixelDelta().x()` when available, else
       `angleDelta().x()/8` (px-equivalent). `progress = clamp(|accum| /
       THRESHOLD, 0..1)`; `side = back` if `accum > 0` (swipe right) else
       `forward`. Drive the indicator. Cap the indicator when the controller
       can't go that way (bounce).
     - `ScrollEnd` → if `progress >= 1` **and** the controller can move that
       way → commit (`back`/`forward` + restore); else cancel (indicator eases
       out). Reset gesture.
   - **Phase fallback** (some Wayland/X11 touchpads report `NoScrollPhase`):
     a single-shot ~120ms inactivity `QTimer`, reset on every wheel event; on
     timeout it stands in for `ScrollEnd`. So "release" is detected whether or
     not phases are delivered.
   - `THRESHOLD`: ~90–110px of accumulated `pixelDelta`; tuned live.
   - On commit, calls back into the main window to restore the returned entry.

3. **`SwipeIndicator`** — the circle+arrow overlay (in `swipe_nav.py`).
   - A frameless child `QWidget` over the content area with
     `WA_TransparentForMouseEvents` (never eats clicks).
   - `set_progress(side, p)` with `p` in 0..1: painted with `QPainter` — a
     ~40px circle that slides in from the relevant edge (left for back, right
     for forward) and scales/fades with `p`; arrow glyph `‹` (back) or `›`
     (forward). A `capped/dim` flag renders the bounce state.
   - `dismiss()` animates/eases out to `p = 0`.

4. **Screen hook (optional protocol).** Screens may implement:
   - `nav_token() -> object | None` — current in-page state.
   - `nav_restore(token) -> None` — apply a token **without recording new
     history**.
   - Only **Calendar** implements it initially (`token = (view, anchor)`); every
     other screen returns `None` and its entry is just the section key. Files
     folder-history is an easy later addition and is **out of scope** here.

### Wiring in `main.py`

- Construct `self.nav = NavController()` and seed it with the initial location
  once the first screen is shown.
- **Recording:** a single `_record_location()` = `nav.visit(NavEntry(key,
  screen.nav_token() if hasattr else None))`, called from:
  - `_sync_chrome` (fires on any `QStackedWidget.currentChanged`, i.e. every
    section switch however triggered), and
  - a new Calendar signal emitted after each in-page navigation
    (`_set_view / _shift / _go_today / _open_day`).
  Both go through `nav.visit`, which dedups identical consecutive entries.
- **Restoring:** `_restore(entry)` sets `self._restoring = True`, switches the
  stack to `entry.screen_key`, calls `screen.nav_restore(entry.token)` when the
  screen supports it, then clears the guard. `_record_location()` returns early
  while `_restoring` is set, so restoration never pollutes history.
- Install `SwipeNavigator` on the content area; give it the `NavController` and
  `_restore`, and parent the `SwipeIndicator` to the content area.

### Calendar refactor

The calendar stops owning navigation history and the wheel gesture:

- **Remove** `_nav_history`, `_nav_future`, `_swipe_accum`, `_record`,
  `_nav_back`, `_nav_forward`, `wheelEvent`, and `SWIPE_THRESHOLD`.
- Each user navigation (`_set_view`, `_shift`, `_go_today`, `_open_day`) instead
  emits a signal (e.g. `state.nav_location_changed` or a calendar-local signal
  main connects to) after updating `view`/`anchor`, so main records
  `("calendar", (view, anchor))`.
- Add `nav_token()` → `(self.view, self.anchor)` and `nav_restore(token)` →
  set `view`/`anchor`, `_sync_seg()`, `refresh()` — no signal emission on
  restore.

### Testing (headless)

- `NavController`: unit tests for `visit` append, consecutive-dup dedup,
  forward-tail truncation after a mid-history visit, `back`/`forward`
  boundaries, cap trimming.
- `SwipeNavigator`: feed synthetic `QWheelEvent`s —
  - phase path: begin/update(past threshold)/end → commit; begin/update(below)/
    end → cancel; horizontal-below-threshold and vertical-dominant → pass
    through / no commit;
  - fallback path: `NoScrollPhase` updates then timer fires → commit/cancel.
  Assert `NavController.back/forward` and `_restore` are called correctly.
- `SwipeIndicator`: construct, `set_progress` across states, render offscreen
  (smoke; no crash).
- Calendar: `nav_token()`/`nav_restore()` round-trip; navigations emit the
  signal; restore does not.
- New test files under `tests/ui/` (e.g. `test_swipe_nav.py`,
  `test_nav_history.py`), consistent with existing `tests/ui/*_v3.py` naming.

**Live check still owed (Josh only):** real two-finger trackpad on Wayland —
phase delivery, direction, and the release-commit feel. Same caveat that was
flagged on the original #18.

---

## Part B — Adaptive "Add X" (#19)

### Behavior (agreed)

A bare `Add X` in the Ask Lumen bar (no time, and no explicit "to my
calendar/todos") is decided by the current page:

- **Calendar page** → calendar event.
- **Every other page** (Todos, Today, Mail, Files, Books, Canvas) and the
  hotkey launcher / Chat composer (no page surface) → todo.

Explicit signals always override the page:
- "add X **to my calendar**" → event anywhere (existing `EVENT_HINT`).
- "add X **to my todos**" → todo anywhere (existing `TODO_TASK_HINT`).
- "add X **at 3pm**" / any `SCHEDULE_SIGNAL` → event anywhere.

### Implementation (`lumen/daemon/router.py`)

1. **Plumb the surface in.** The daemon dispatch (~L786) reads
   `surface = (payload.get("context") or {}).get("screen")` and passes
   `surface=surface` to `_chat`. The Ask Lumen bar already sends
   `context["screen"]` for every page (`main._ask_context`), and every screen's
   `context()` includes it. `_chat` gains `surface: str | None = None`.

2. **One new branch, ahead of the existing ADD_LOOSE→todos branch:**
   ```python
   elif (surface == "calendar" and (am := ADD_LOOSE.match(message))
         and self._confirm is not None and self._bridge is not None
         and self._calendar is not None
         and not TODO_TASK_HINT.search(message)
         and not FILE_TASK_HINT.search(message)):
       # Calendar page: a bare "add X" is an event, not a todo (#19).
       sub, subsystem = self._create_event_chat(message), "calendar"
   ```
   The existing branch is unchanged and now only reached for non-calendar
   surfaces:
   ```python
   elif ((am := ADD_LOOSE.match(message))
         and not SCHEDULE_SIGNAL.search(message)
         and not FILE_TASK_HINT.search(message)
         and not TODO_TASK_HINT.search(message)):
       sub, subsystem = self._nl_add_chat(am.group(1).strip()), "todos"
   ```
   - `TODO_TASK_HINT` / `FILE_TASK_HINT` still exclude explicit todo/file adds
     from the calendar branch.
   - `SCHEDULE_SIGNAL` adds already route to `EVENT_HINT` on any surface — the
     calendar branch also catches them (event is correct either way).
   - Calendar-unavailable (`_calendar is None`) falls through to the todo
     default.

3. **No other decisions become surface-dependent** in this change — scope is
   the ambiguous-add tie only. The `surface` parameter is a general seam that
   future adaptive decisions can reuse, but nothing else reads it yet (YAGNI).

### Testing (`tests/daemon/test_router.py`, deterministic — no model)

- `Add dentist appointment` with `surface="calendar"` → event path.
- Same message with `surface="todos"`, `surface=None`, `surface="mail"` → todo.
- `Add X to my todos` with `surface="calendar"` → still todo (explicit wins).
- `Add X to my calendar` on any surface → event.
- `Add X at 3pm` on any surface → event.
- `add a todo: X` (explicit `TODO_ADD`) unaffected by surface.

---

## Out of scope

- Files/mail/other-screen in-page history (only Calendar participates now).
- Any change to the daemon's model routing, thermal, or MCP layers.
- Generalizing `surface` beyond the ambiguous-add decision.
