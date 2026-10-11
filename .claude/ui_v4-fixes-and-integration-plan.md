# ui_v4: fixes and integration plan

Written 2026-10-10 from a review of `lumen/ui_v4/` at commit `f41e5bb`. Line
numbers below refer to that commit. Check them again before editing, because
they will drift.

## Status (end of 2026-10-10 session)

Steps 1–4 of "Suggested order of work" are done, uncommitted. Josh answered
section 5 (recorded there). Full suite: 1915 passed, 19 skipped.

- **Done:** section 4's two shared one-liners; every H, M and L item in
  section 2; every section 3 design item except the under-600px rail (dropped,
  as recommended) and the two Josh overrode (Today keeps the hour grid; the
  Ask bar answers inline). `.claude/skills/ui-spec.md` now exists (section 4).
- **Found and fixed on the way:** "Review & create" had the same `&` bug as L1;
  `MailRow`'s fixed 76px minimum squashed rows with label chips; `EmptyState`
  clipped long body text; L15's real cause was the daemon caching only the
  calendar's colour (new `events.event_color` column); compose revise, the
  briefing, the commitment rescan, Books and Files edit suggestions could all
  stick on "busy" after a daemon error; the eager QtWebEngine import cost
  ~33 MB RSS / ~30 ms per launch (dropped; lazy import verified).
- **A code review of the batch found six more, all fixed with tests:** a
  quick-captured todo's confirmation was overwritten by the "no answer" text
  in the Ask bar; a failed file open stashed a stale draft that later beat
  the saved file; calendar edits stamped today's UTC offset, so an event
  across a DST change moved an hour (v3 too — fixed in both); multi-day timed
  events could be cut short by the edit dialog (times now hidden for them);
  the Canvas page stayed Active after a successful sign-in; the archived v2
  calendar wiped its events on an error reply.
- **Tests:** `tests/ui/*_v4.py` (14 files, shared helper `v4_support.py`).
- **Left:** step 5 (Josh's live visual pass, including Canvas sign-in and
  the Super+L toggle on Hyprland), then step 6 (switch-over — Josh said yes,
  after his pass). Nothing is committed yet.

## Summary

ui_v4 (the "Field Notes" redesign) is close to done. All 9 screens are built,
load real data, and ran without a single uncaught exception against the live
daemon, in light, dark and narrow windows. Every call into `AppState` was
checked against `ui_v3/state.py` and `ui_v2/state.py`, and they all exist with
the right signatures. The confirm gate for outside writes is intact. What's left:

- 2 high-severity bugs, about 9 medium ones and a tail of small ones (section 2)
- design-plan items that were never built (section 3)
- 5 product decisions only Josh can make (section 5)
- the switch-over itself, which is small (section 6)

There are no ui_v4 tests yet.

---

## 1. Background: where v4 came from

- **The design source** is the Open Design desktop app's project "Lumen UI review
  and redesign":
  `~/.local/share/open-design/namespaces/aur/data/projects/5305ac28-b077-4301-9bdd-43f57bf9ea38/`
  - `review.md` scores ui_v3 and has a **"How v4 fixes each problem"** table.
    That table is the spec. It also requires **feature parity with ui_v3** and
    keeps the confirm flow, page-aware asking and one shared conversation.
  - `index.html` is the clickable HTML mockup. Its script part was never
    finished (Josh said to skip it), so treat it as a static visual reference.
  - The design system is the Open Design project `ds-field-notes`, in the same
    `projects/` folder (`DESIGN.md`, `tokens.css`, `components.html`).
  - The build chat is in Open Design's SQLite DB, `data/app.sqlite`: table
    `messages`, joined through `conversations.project_id = '5305ac28-…'`.
- **History.** The Open Design agent hit its session limit three times
  (Sep 24–25). Its last message said only the base and 2 screens existed. The
  remaining screens were finished afterwards and committed on 2026-10-09 as
  `e98d7f4 feat(ui_v4): …` (opt-in, `lumen-ui-v4`). Nobody had launched or
  reviewed v4 before 2026-10-10.
- **What v4 depends on.** It isn't standalone. It imports:
  - `ui_v3`: `state.AppState`, `swipe_nav`, `nav_history`, `canvas_login`, `canvas_creds`
  - `ui_v2`: `daemon_client`, `single_instance`, `tray`, `mail_html`

  **Don't delete ui_v3 or ui_v2 when switching over.** Moving these modules into
  v4 (or a shared package) is a separate, later cleanup.

---

## 2. Bugs

Severity reflects daily-use impact. "(v3 too)" means ui_v3 has the same flaw.
Fix those in the shared code where one exists.

### High

**H1. "Label this email" from the Inbox Ask bar doesn't know which email is open (regression of v3 #11)**
- **Where:** `screens/ask.py:573-577` only sends `open_email` when
  `ctx["message"]` is a dict. `MailScreen.ask_context()` (`screens/mail.py:383`)
  returns a **str**, which matches the contract in `screens/__init__.py`. So
  `open_email` is never sent, and `router._open_email_context`
  (`daemon/router.py:2087`) gets nothing.
- **Fix:** in `ask.py` `_send_text`, when `ctx["screen"] == "mail"`, build
  `open_email` from `self.state.sel_mail()` instead of reading it from `ctx`.
  Add a test that asserts the chat payload has `open_email` when the question is
  asked from Inbox with a message open. Note that the daemon only reads
  `context.screen`, `file` and `dir` (`router.py:1059`, `2079-2085`); the str
  summaries are ignored, as v3's extra keys were.

**H2. Suggest labels can lock the Mail screen until restart (v3 too)**
- **Where:** when the daemon replies with an error, `DaemonClient` pops the
  callback and only emits `error` (`ui_v2/daemon_client.py:95-100`). The
  `_Busy` scrim in `screens/mail.py:205-214` waits for a callback that never
  comes, swallows every click, and the button stays on "Sorting…".
- **Fix:** give `DaemonClient.request()` an optional `on_error` callback, called
  with the error message. Use it here to hide the scrim and toast the error.
  This is the same root cause as M2, so fix both together.

### Medium

**M1. Reopening the window from the tray shows stale data**
- **Where:** v4 screens only refresh in `on_shown`, which runs only from
  `switch_to` (`main.py:335-346`). The tray's "show" just calls `win.show()`
  (`app.py:95-98`). v3 did this work in each screen's `showEvent`: rebuild if
  dirty, plus `sync_inbox` / the #59 calendar sync / Manabi. Affected:
  - Today (`today.py:182-184`, `221-223`)
  - Mail (`mail.py:366`)
  - Calendar (`calendar.py:626`, `796`)
  - Todos (`todos.py:309`)
- **Fix:** when the window becomes visible again, re-run the current screen's
  `on_shown()` through `_call_shown`. Do this in `LumenWindow.showEvent`, or in
  `present_window()` in `app.py`. Throttle it so focus flapping doesn't hammer
  the daemon.

**M2. Loading skeletons stay forever on error or with the daemon offline**
- **Where:** the `_seen` / `_loaded` / `_ev_loaded` flags only flip inside
  success callbacks:
  - `mail.py:620-624`, `729`
  - `calendar.py:888-895`
  - `today.py` (`_ev_loaded`, `_seen_todos`, `_seen_mails`)

  So the "not connected" empty states can't be reached in exactly the case they
  exist for.
- **Fix:** use the `on_error` path from H2 to flip the flags and show the
  empty or not-connected state. Add a test with a fake client that errors.

**M3. Saved text size isn't applied at startup**
- **Where:** `install_text_size()` (`screens/settings.py:83`) is only called
  from the Settings, Books, Files and Canvas constructors. The app starts at
  100% and jumps in size when one of those screens is first opened.
- **Fix:** call it once in `app._apply_app_style()` (and in `build_window()` for
  screenshot scripts). Also decide on migrating v3's saved size: v4 stores it in
  `QSettings("lumen","ui_v4")`, so v3's choice doesn't carry over. Read v3's
  value once if v4 has none.

**M4. Clicking a tag chip on a todo row also opens the edit card**
- **Where:** `TagChip.mousePressEvent` calls `super()`, and the ignored event
  propagates to the parent `ClickRow` (`components.py:766-769`, `867-870`;
  `todos.py:135`).
- **Fix:** accept the event in `TagChip` and don't call `super()` after firing.

**M5. The AI model can load at launch with no user action (power rule)**
- **Where:** `main.py:327-328` restores `last_screen`. If that was Ask,
  `_sync_chrome` (`main.py:598-599`) calls `warm_model()` at login. v3 always
  opened on Today.
- **Fix:** don't warm on the restore at startup; only warm on a user-initiated
  switch. Alternatively, always start on Today.

**M6. The "turn the model on" highlight in Settings is wiped right away**
- **Where:** `_flash` (`settings.py:472`) styles the current `model_card`. The
  replies to `on_shown`'s requests rebuild that card within milliseconds
  (`settings.py:227`, `269`, `275`).
- **Fix:** port v3's `_flash_model` flag, which the rebuild honours (v3
  `settings.py:181`, `288`).

**M7. Settings → Advanced shows wrong or empty data (v3 too)**
- **Where:**
  - It reads `snap["mcp_servers"]` (`settings.py:746`), but the daemon sends
    `snap["mcp"]["servers"]` (`daemon/settings_snapshot.py:50`). So it always
    says "No servers configured".
  - `config_path` isn't in the snapshot, so the path shown is hardcoded
    (`settings.py:718`).
  - The config keys `context`, `idle_timeout`, `interval`, `on_wake` and
    `confirm_writes` don't exist. The snapshot has `num_ctx`,
    `idle_unload_minutes` and `gmail_poll_minutes`. The section shows defaults
    under "values Lumen is running with" (`settings.py:738`, `803-813`).
- **Fix:** read the real keys, and add `config_path` to the daemon snapshot.

**M8. The #48 "edit moved the todo out of the filter" notice never fires (v3 has the same early return; v4 added the recheck on the wrong branch)**
- **Where:** `todos.py:391-396`. `update_todo` is async, so the todo is always
  still in the filtered list when `note_edited` runs. The early return wins and
  the 400 ms recheck is never scheduled. The row then silently vanishes.
- **Fix:** always schedule the recheck, or run the check from the todos-changed
  signal.

**M9. Canvas browser keeps running after you leave Canvas (power; v3 too)**
- **Where:** once built, the QWebEngineView and its page live until the app
  quits. `_done_browsing` (`canvas.py:1125`) only switches pages. If you leave
  through the sidebar while browsing, `_cred_timer` keeps running JavaScript
  every second on the hidden page (`canvas.py:1140-1159`).
- **Fix:** stop `_cred_timer` in `hideEvent`. When leaving the browser, load
  `about:blank` or `deleteLater()` the view (the persistent profile keeps the
  cookies).
- **Also:** `app.py:35` imports `QtWebEngineWidgets` at every launch, and v3
  doesn't. v3 works with only `AA_ShareOpenGLContexts` set before
  `QApplication`. Measure startup RSS with and without the import. If it costs
  memory, drop it as v3 does, and confirm Canvas Connect still works.

### Low

- **L1. Compose button reads "Review _send".** The `&` is treated as a mnemonic.
  Use `&&` in the label (overlays.py, ComposeOverlay foot).
- **L2. Back/forward can restore a view you never saw.**
  - `_sync_chrome` records history (`main.py:591`) before `_call_shown` applies
    the kwargs (`main.py:346`). So `switch_to("calendar", view="day", …)` or
    `switch_to("mail", mid=…)` records the old state.
  - `calendar.py:626-645` doesn't emit `nav_location_changed`.
  - **Fix:** record after `on_shown`, or have screens emit their location after
    applying kwargs.
- **L3. Page context is lost when Ask is busy.** If a turn is still streaming,
  `start()` puts the question in the composer but drops its context
  (`ask.py:286-292`, `528`). Keep the pending context with the draft.
- **L4. Screen-level dialogs aren't registered as modal.** EventEditOverlay,
  TodoDetailOverlay and the new-label dialog are missing from `_modals`
  (`main.py:285`, `608`). Ctrl+1–8 and Ctrl+K act behind them, and the dialog
  stays on top of the next screen. Register them.
- **L5. Events ending at or after midnight can't be edited.** `hhmm` clamps to
  "24:00" (`calendar.py:77-79`, `504`), which fails `_HHMM` (line 63). Even a
  title-only edit errors. Accept 24:00, or carry the end date.
- **L6. A deferred todo delete is lost if the app quits within 5.2 s**
  (`todos.py:430-435`). Flush pending deletes on `aboutToQuit`.
- **L7. Screen-started confirms carry a `"toast"` key that ConfirmOverlay never
  shows** (`canvas.py:1446`, `1734`; `settings.py:619`; `overlays.py:260`), so
  Disconnect and Remove all give no feedback. Show it in the overlay's
  approve path.
- **L8. Gmail Disconnect copy says only Gmail stops.** The daemon drops the
  shared Google token, so Calendar disconnects too (`router.py:1270`). Fix the
  wording.
- **L9. Saving a rule doesn't refresh the Settings rule list (v3 too).** Refresh
  after the rule dialog saves.
- **L10. Books Refresh can start two recommendation runs.** Any
  `recs_changed` resets `_busy` mid-generation (`books.py:277`). `on_shown` also
  re-fires when Books is re-selected. Only clear busy on the run's own reply.
- **L11. Canvas "Save this login?" can pop with an empty password (v3 too).**
  - Cause: `_maybe_save_login` runs on the first replay of a stored session
    cookie (`canvas.py:1190`).
  - Related: Settings → Disconnect Canvas doesn't clear the browser cookie jar,
    so the next Connect silently reuses the old session.
- **L12. Files: back/forward and `open_path` replace an editor with unsaved
  changes without warning** (`files.py:197`, `217`). Only Close has Undo.
  Prompt, or keep the draft.
- **L13. Arrow-key mail browsing (#50) is probably dead in real use (v3 too).**
  - Why: a clicked row takes focus, ignores Up/Down, and the scroll area eats
    them.
  - The tests call `keyPressEvent` directly
    (`tests/ui/test_mail_scroll_keys_v3.py:80-82`), so they can't catch it.
  - Needs a live check, then a window-level key handler.
- **L14. Ctrl+Enter sends mail directly.**
  - What happens: Compose binds Ctrl+Enter to send (`overlays.py:340`), and
    `emails.send` has no daemon gate (`router.py:3066`: "the Send click is the
    confirmation").
  - The caption says "you confirm before it goes" (`overlays.py:293`), and the
    button is "Review & send", but there's no review step.
  - Either add a review/confirm step or fix the wording. CLAUDE.md wants
    explicit confirmation for sends, so check with Josh if unsure.
- **L15. Calendar colours ignore Google's per-event colour (parity gap).**
  `event_color` hashes the calendar name into 5 slots (`calendar.py:71-74`). v3
  painted the event's own `color` (v3 `theme.py:165-171`), so changing a colour
  in the edit dialog has no visible effect.
- **L16. Forward silently drops attachments** (`mail.py:866-876`). Say so in the
  dialog, or attach them.

---

## 3. Design-plan items not built

These come from review.md's "How v4 fixes each problem" table. Items marked
**(decision)** were kept on purpose by whoever finished v4, or are product
calls, so check section 5 before changing them.

- **Files**
  - Hidden files should be **off by default**, with a "Show hidden files"
    switch. `_entries` (`files.py:287`) passes dotfiles through, so the home
    folder opens on `.android`, `.cache`, `.cargo`…
  - Add a list/grid toggle (grid only today).
  - Add an **"Ask about this file"** button. Today you can only ask from the
    bottom bar.
  - Open the editor as a preview/editor pane beside the folder view. Today it
    replaces the folder view full-page.
  - Folders over 500 entries are cut off silently (v3 too). Show "N more not
    shown".
- **Today**
  - **(decision)** The schedule should be a time-ordered list, with "Nothing on
    the calendar…" at the top and a *New event* button. It's still an hour grid
    (`today.py:434-446`; the docstring keeps it on purpose, citing #5).
  - The "Due today" card shows the total open count (`open_count()`,
    `today.py:388-390`), which recreates the "6 open, 3 shown" mismatch the
    review complained about. Show the due-today count, plus an "All todos (N)"
    link.
- **Mail**
  - **(decision)** Remove the per-row trash icon (`mail.py:98-100`).
  - Make Delete in the reading pane the **danger** variant (`mail.py:810`,
    currently secondary).
  - Use one primary per view: Reply should be secondary, since Compose is
    primary (`mail.py:300`, `802`).
  - On narrow windows, show mailboxes as a chip row rather than behind a toggle
    (`mail.py:418-451`).
- **Calendar.** Out-of-month days should use muted text only, with no
  background tint (`calendar.py:394-400` paints `bg` vs `surface`).
- **Todos.** Move the tag filter and "Show completed" directly above the list
  (`todos.py:300-303`, `621-645`; they're in the right rail).
- **Ask.** The empty state should offer **three suggested prompts**
  (`ask.py:470-475` has one "Write a question" button).
- **Command palette.** Add "Morning briefing", and show shortcut hints on action
  commands (`overlays.py:865-885`).
- **Settings**
  - Explain all three model modes, not only the selected one
    (`settings.py:337`).
  - Put Canvas Connect in Accounts. Today it's a button that navigates to the
    Canvas tab (`settings.py:597`).
- **Model naming.** Use plain words ("On this computer" / "On-device") instead
  of the raw tag `Local · qwen3:4b-instruct` in the sidebar status line
  (`main.py:495`) and the answer footer (`ask.py:648`). The Ask header badge
  already does this right.
- **Reduced motion.** It should follow the system setting. Today it only reads
  `LUMEN_REDUCED_MOTION` or the in-app toggle (`theme.py:160-164`). On Linux,
  read the desktop's animation setting where it's available
  (`gtk-enable-animations`, or KDE's `AnimationDurationFactor`).
- **Under-600px top rail.** It's unreachable, because `MIN_W` is 760
  (`theme.py:146`). Recommend dropping this from the spec: it's a laptop app.

---

## 4. Shared with ui_v3, and unrelated finds

- **`what&#39;s` in mail previews.** Neither UI unescapes Gmail's HTML-escaped
  snippets. Add `html.unescape` in `_norm_mail` (`ui_v2/state.py:83`). One line
  fixes v3, v4 and the suggest-review popup.
- **Two daemon tests started failing on 2026-10-10:**
  - `tests/daemon/test_router.py::test_scan_commitments_one_shot_finds_and_lists`
  - `tests/daemon/test_router.py::test_promise_chat_scans_and_answers`

  The fixture's sent mail is dated `2026-07-12`, and the commitment scan looks
  back `FIRST_RUN_DAYS = 90` (`daemon/llm/commitments.py:23`), so it aged out
  today. Make the fixture dates relative to now. The other 1795 tests pass.
- **No `ui-spec.md` exists**, though the `ui-designer` agent definition says it
  reads one. Point it at this plan and review.md, or write a short `ui-spec.md`
  for v4.
- **`scripts/screenshot.py` captures blank screens.** It steps through raw
  QStackedWidget indices, which no longer works with lazy screens. Use
  `win.switch_to(key)`; see the snippet in section 7.

---

## 5. Product decisions owed (ask Josh; don't decide these)

1. **Make v4 the default** for `lumen` / `lumen-ui`, keeping v3 as `lumen-ui-v3`?
2. **Super+L.** v3 shows a small floating ask box (`ui_v3/launcher.py`): it
   streams without raising the window, a second press closes it, and it warms
   the model. v4 has no launcher; `toggle-launcher` raises the whole window with
   the Ask bar focused (`app.py:100-108`), and a second press doesn't hide it.
   Port the v3 launcher in Field Notes style, or keep v4's behaviour?
3. **Ask bar.** v3's bar answered inline on the page, kept the turn out of
   history (#24), and offered "open in Chat". v4 jumps to Ask Lumen and saves
   every bar question to history. The spec intends v4's "one conversation", but
   #24 existed because of the history side effect.
4. **Accent colour picker.** It's gone in v4 (v3 `_accent_section` /
   `accent_requested`), because Field Notes uses a fixed forest green. Okay to
   drop?
5. **Today's hour grid and Mail's per-row trash icon.** Follow review.md
   (schedule list; delete only in the reading pane), or keep them?

**Josh's answers (2026-10-10):**
- default: **yes**, after his live visual pass
- Super+L: **raise the window; a second press hides it**
- Ask bar: **answer inline on the page, like v3** (not saved to history, "Open in
  chat" to continue) — overrides review.md
- accent picker: **drop**
- Today hour grid: **keep it** — overrides review.md
- Mail per-row trash: **remove** (Delete in the reading pane, danger variant)
- L14 send: **add a real review step** — button and Ctrl+Enter both show a
  "Send this to X?" confirm first

If Josh just says "go", use review.md's choices:
- default: yes
- Super+L: keep v4's behaviour, but make a second press hide the window
- Ask bar: v4's jump-to-Ask
- accent picker: drop
- hour grid: replace with the list
- per-row trash: remove

---

## 6. Integration: switching v4 on as the default

Do this after the H and M bugs are fixed, tests exist, and Josh has done a live
visual pass.

1. **`lumen/launch.py`**
   - Swap `from lumen.ui_v3 import SOCKET_NAME` → `lumen.ui_v4` (lines 99 and
     116).
   - Swap `from lumen.ui_v3.app import main as ui_main` → `lumen.ui_v4.app`
     (line 158).
   - Have `quit_running()` also ask the `lumen-ui-v3` socket, so an old v3
     window still quits. The process sweep already catches both, because
     `instance_lock.is_lumen_argv` matches any argv starting with `lumen`.
   - v4's `app.main()` already honours `LUMEN_UNIFIED=1` (close quits the app
     and the daemon) and handles `toggle-launcher` / `show-launcher` / `quit` /
     `show`. Verify each one live.
2. **`pyproject.toml`**
   - Point `lumen-ui` at `lumen.ui_v4.app:main`, and keep `lumen-ui-v3` and
     `lumen-ui-v4`.
   - Run `uv sync` afterwards so `.venv/bin/` entry points regenerate.
   - `~/.local/bin/lumen` is a shim to `.venv/bin/lumen` → `launch.main`, so
     launch.py changes take effect immediately.
3. **Hyprland.** `~/.config/hypr/UserConfigs/UserKeybinds.conf:32` binds
   Super+L to `lumen --toggle-launcher`. That keeps working; its behaviour
   depends on decision 2. `~/.local/share/applications/lumen.desktop` runs
   `~/.local/bin/lumen` and needs no change.
4. **Settings carry-over.**
   - v4 keeps its own QSettings, `("lumen","ui_v4")`, with the keys `theme`,
     `text_size`, `reduced_motion`, `last_screen` and `geometry`.
   - v3's text size (and window geometry, if wanted) won't carry over unless it
     is migrated once on first run.
   - Daemon-side state (model mode, connections, rules, Canvas session) is
     shared, because both UIs talk to the same daemon. The Canvas web profile
     `lumen-canvas` is also shared.
5. **Docs.**
   - Update `.claude/CLAUDE.md`'s directory block (ui_v4 primary).
   - Update the README "Launch" section and `lumen/ui_v4/README.md` ("ui_v3 is
     still the default").
   - Add a memory note.
6. **Later, optional.** Move the seam modules v4 borrows from ui_v2/ui_v3 (see
   section 1) into v4 or a shared package, then retire v3. Keep v3 runnable
   until v4 has been the daily driver for a while.

### Tests to add (none exist for v4)

Copy the patterns in `tests/ui/*_v3.py` (pytest-qt, offscreen, fake daemon
clients). Minimum set:
- **Smoke:** build the window in sample mode (`build_window(None)`), then
  `switch_to` every key with no exceptions. Include a dark-theme pass.
- **Regression tests:**
  - H1: payload has `open_email`
  - H2/M2: an error reply clears the busy/skeleton state
  - M3: text size at startup
  - M4: a tag click doesn't open the editor
  - M5: no warm on startup restore
  - M8: the #48 notice fires
  - L1: compose label text
- **Confirm gate:** archive/delete/event writes go through `confirm.open`, or
  the daemon gate, never straight to a write.
- **Perf guard** like `test_perf_v3.py`: a hidden screen does no rebuild work on
  a background poll.

---

## 7. Notes from the 2026-10-10 session

- **How it was verified.** I started the daemon (`uv run lumen-daemon`; nothing
  was running), then rendered every screen offscreen against real data. I also
  sent one real Ask question through `win.ask(...)` from Calendar. It used the
  list-events tool, answered "ten" (correct), and showed "Asked from Calendar".
  I deleted that conversation and stopped the daemon afterwards.
- **Offscreen render recipe** (worked well; the scratch copy is gone):

  ```python
  # QT_QPA_PLATFORM=offscreen uv run python shoot_v4.py OUT 1280x800 light
  from lumen.ui_v4 import app as v4app, theme as T
  v4app._prepare_webengine(); app = QApplication(sys.argv)
  qs = QSettings("lumen", "ui_v4"); prev = qs.value("theme"); qs.setValue("theme", theme)
  v4app._apply_app_style(app)
  cfg = load_config(); T.MODEL_NAME = cfg.model; sp = str(cfg.socket_path)
  data, chat, confirm, side = (DaemonClient(sp) for _ in range(4))
  state = AppState(data=data, chat=chat, confirm=confirm)   # ui_v3.state.AppState
  state.attach_confirm_source(side); state.attach_compose_source(side)
  win = build_window(state, chat); win.resize(w, h); win.show()
  for k in ["today","mail","calendar","todos","books","files","canvas","ask","settings"]:
      win.switch_to(k); pump(3.5); win.grab().save(f"{OUT}/{k}.png")
  # overlays: win.command_palette.open(), win.compose.open({...}),
  #           win.event.open({...}), win.confirm.open(payload, cb), win.show_toast(msg, undo=fn)
  # restore qs "theme" to prev afterwards (it's Josh's real preference), and
  # print(..., flush=True) before os._exit() or the output is lost.
  ```
  `pump(s)` is a `processEvents()` loop with a short sleep. Three and a half
  seconds per screen was enough for real data to arrive.
- **Opening a mail marks it read.** `state.select_mail()` → `auto_read`.
  Arrow-key browsing uses `select_mail_quiet`. The Mail screen only calls
  `select_mail` on a click, or when opened with `mid=…`. Inbox unread dropped
  from 6 to 2 during the session, but it wasn't v4: the script never clicked a
  message, so it was external (Gmail read elsewhere).
- **Model mode.** Josh's DB is in **Claude mode (Haiku)**, so any live Ask test
  spends his Claude quota (Pro plan). Keep live LLM checks to one or two short
  questions.
- **Canvas.** The live QtWebEngine + Duo login can only be tested by Josh. The
  Canvas session was expired as of 10-09 (401). The v4 Canvas screen shows the
  not-connected explainer with "Saved login ready". The connected view (courses,
  assignments, announcements, per-course switches, calendar sync) needs Josh's
  live run.
- **What reviewers confirmed is fine.** No changes needed here:
  - Wiring and confirms:
    - every AppState call and signal
    - confirm overlay exits (Esc answers `confirm_id`; overlapping confirms queue)
    - compose cancel with `compose_id`
    - archive/delete/calendar writes behind the daemon confirm
  - Ask: all chat stream handlers, including mid-stream confirm and compose,
    `model_off` and `claude_unavailable`.
  - Window: lazy screens with retry-on-failure, and all icons resolve.
  - Canvas: the web view is built lazily (only on Connect, Open or a row click),
    and the shared `lumen-canvas` cookie profile, autofill/keyring and Open↗
    links are at parity.
  - Settings: model mode Off / On this computer / Claude, Claude model choice,
    Pause/Disconnect, habits approve/dismiss.
  - Files: writes only on an explicit Save.
  - Better than v3:
    - Calendar ignores out-of-order replies and refreshes after edits.
    - Mail rebuilds are as efficient as v3's or better.
- **What v4 adds over v3:**
  - forward, mark as unread and threaded replies
  - todo search, and Undo on todo delete
  - Ctrl+K palette, light/dark/system theme, Ctrl+1–8, and the 64px icon rail
    under 900px
- **Process.** Per `.claude/CLAUDE.md`, use the Development Protocol for the
  build and the Debugging Protocol for bugs. Decide technical choices yourself;
  ask Josh only the section 5 questions. Commits must end with
  `This commit used N prompts.` and carry no AI attribution.

### Suggested order of work

1. The shared one-liners: snippet unescape, and relative dates in the two
   time-bomb tests.
2. H1, H2+M2 (`on_error` in DaemonClient), M1, M3, M4, M5, with tests.
3. M6–M9, then the L items.
4. Section 3 design items, once the section 5 decisions are in.
5. Josh's live visual pass, including Canvas login.
6. Switch-over (section 6), docs and a memory note.
