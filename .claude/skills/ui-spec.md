# Skill: UI spec (ui_v4, "Field Notes")

The desktop UI is `lumen/ui_v4/`: the default since 2026-10-10 (`lumen`,
`lumen-ui`, `lumen-ui-v4`); ui_v3 stays runnable as `lumen-ui-v3`. Read this before changing
any v4 screen, and update it when you add or change behaviour.

## Sources of truth
- **Spec:** review.md's "How v4 fixes each problem" table, in the Open Design
  project `~/.local/share/open-design/namespaces/aur/data/projects/5305ac28-b077-4301-9bdd-43f57bf9ea38/`
  (`index.html` there is a static visual reference only). It requires feature
  parity with ui_v3.
- **Design system:** Open Design project `ds-field-notes` (`DESIGN.md`,
  `tokens.css`). In code: `theme.py` holds every colour literal and token;
  `styles.py` builds the one app stylesheet from QSS roles/properties;
  `components.py` holds the building blocks. Screens never set inline colours.
- **Josh's product decisions (2026-10-10), overriding review.md where noted:**
  - Ask bar answers **inline on the page** (ephemeral, not saved; "Continue in
    Ask Lumen" seeds a conversation) — overrides review.md's jump-to-Ask.
  - Today keeps the **hour grid** — overrides review.md's list.
  - Super+L raises the window with the Ask bar focused; a second press hides it.
  - Mail: no per-row trash; Delete is the reading pane's danger button.
  - Compose: "Review & send" and Ctrl+Enter show a "Send this email?" confirm.
    It has a Cc field (#105). Esc and the X ask "Discard this draft?" in the
    footer when there's something to lose; the Discard button discards in one
    click (#106). A Send the chat had stopped waiting for toasts its result
    itself (#111).
  - No accent picker (fixed forest green). No under-600px top rail.

## Shell (`main.py`)
- 232px sidebar (Today / Daily / Library / Ask Lumen, Settings + theme at the
  foot); a 64px icon rail below 900px. Ctrl+1–8, Ctrl+, , Ctrl+K palette,
  Ctrl+L Ask bar, Alt+←/→ history. Shortcuts do nothing while any `_Overlay`
  is open (window's or a screen's).
- Screens are built lazily on first `switch_to(key)`; contract in
  `screens/__init__.py`. `on_shown(**kw)` runs on every switch, and again
  (with `resumed=True`, throttled to 30 s) when the window comes back from the
  tray. History is recorded after `on_shown` applies its kwargs.
- The model is warmed only by a user's switch to Ask, never by restoring Ask
  at login.

## Ask (`askbar.py`, `screens/ask.py`)
- The bar and the Ask screen share one chat connection: one turn at a time
  (`window.chat_busy()`); each ignores stream events it didn't ask for.
- Both run the same stream protocol, `askbar.StreamTurn` (busy gate, wake /
  cold-start wording, empty-turn settle, captured todo, todo refresh); each
  surface only draws, through its `turn_*` methods. Fix stream behaviour
  there, once.
- Page context is built once (`askbar.context_dict` / `chat_payload`). A
  screen's `ask_context()` may return a str (summary) or a dict; Inbox's dict
  carries `message` so `open_email` is sent ("label this email"). The daemon
  puts it in the prompt as "what this page is showing" (router
  `_page_context`, capped at 2,500 chars, #68), so an `ask_context()` should
  say what the user can see, dates included — it is what the answer uses.
- Ask screen empty state: what Lumen can see + three suggested prompts.

## Screen notes
- **Inbox:** rows size to their content (no fixed min height). Up/Down browse
  from a focused row without marking read. Below 900px the mailboxes are a
  chip row. Forward warns that attachments aren't carried.
  - **Starred mailbox (#40):** `SCOPES` order is Inbox, Unread, Starred, Sent.
    Each MailRow has a star IconButton at its right edge (outline/`fg2` = unstarred;
    `star-fill`/`warn` = starred). Clicking the star must NOT open the message or
    mark it read — the star button is a QPushButton child that absorbs the click
    before it reaches ClickRow's mousePressEvent. `can_update_to` / `update_from`
    handle `is_starred` in-place. Reading pane action row has a Star/Starred ghost
    button. Pressing `s` on the screen toggles the star on the focused/open message
    (Gmail convention). In the Starred scope, unstarring removes the row from the
    list immediately (optimistic). Empty Starred state: "No starred mail" / "Star a
    message to keep it here." — icon `star`. The `warn` token (amber/ochre) is the
    star's filled colour; screens never set inline colours, only token names.
  - **HTML bodies (#30):** `MailBody` builds `HtmlBody(mail=raw, width=…)`,
    which runs `mail_html.fit_html` at its real width and again whenever the
    pane crosses a 40px step. Never hand it `prepare_html` output for mail: the
    reading pane is ~376px at a 1280px window, not the 620 `prepare_html`
    assumes, and only the measured choice keeps CSS-built mail (receipts)
    from collapsing without breaking other mail.
  - **Plain-text bodies (#101):** When `body_html` is absent `MailBody` renders
    the plain `body` field through `mail_html.plain_to_html()` and puts it in an
    `HtmlBody` — never a raw `Label`. This prevents Qt's AutoText heuristic from
    silently mangling `<`/`>` characters and makes URLs clickable.
  - **Dark bands (#102):** `prepare_html` flattens mail to dark-on-white: pale
    text colours are dropped, every `bgcolor=` attribute is dropped, and so is
    every CSS background too dark to carry `CARD_FG` (luminance < 0.24, i.e.
    under 4.5:1). Per-element rules don't work: the band is usually a container
    and the white text an element inside it. Mid-tone brand colours stay.
  - **Unread counts (#103):** the sidebar badge, the Unread scope count,
    Today and the Ask context count the INBOX (`state.unread_count()` from the
    daemon's `inbox_unread` tally), whatever list Mail shows. The Mail header
    counts the list under its own title.
  - **Reloads (#109):** star / label / mark-unread / archive reload the view
    in force — a search re-runs (`state.mail_query`) — and keep the open
    message's fetched HTML, so the reader doesn't flash or scroll. When the
    open message leaves the view, the one below it opens (else above).
  - **Suggested labels (#107):** Esc, an outside click and the review's X only
    close it; the rest wait behind the "N to review" banner. Only "Dismiss
    all" drops them.
  - **Remote images (#112):** `inline_remote_images` fetches with a bounded
    thread pool (_POOL_SIZE = 6) and an overall per-message deadline
    (_TOTAL_DEADLINE = 8 s); images not collected by the deadline are dropped (no
    broken boxes, message shows immediately). `_fetch_data_uri` refuses hosts
    that resolve to loopback/private/link-local addresses (SSRF guard).
    `MailBody._loaded` is a bounded LRU (50 entries) so inlined HTML doesn't
    grow for the life of the process.
- **Todos:** tag chips + Show completed sit directly above the list; the rail
  has progress and group-by. A tag chip click never opens the edit card. The
  "Moved out of #tag" notice judges an edit against the filter it was saved
  under; changing the filter ends the watch.
- **Books:** Refresh's busy state belongs to its run — a timed-out run's late
  reply can't end a newer one.
- **Files:** hidden files off by default (switch, remembered), list/grid
  toggle (remembered), "N more not shown" past 500 entries. An open file sits
  in an editor pane beside a folder list (≥960px), full page when narrower.
  The panes rebuild separately; unsaved edits are never rebuilt away, kept as
  a draft when you navigate off the file, and Close offers Undo. Quitting
  asks Save / Don't save / Cancel when anything is unsaved (#88). A file that
  changes on disk reloads if unedited, else asks "Reload from disk / Keep
  mine"; Save never writes over a newer disk version (#86). A suggested edit
  shows as a diff and merges into what you typed meanwhile (#87).
- **Calendar:** events paint in Google's colours (the event's own, else its
  calendar's); the legend uses calendar colours. Out-of-month days are muted
  text on the same ground. The edit dialog accepts 24:00 and reads an end at
  or before the start as the next day.
  - **Multi-day / past-midnight events (#113):** the daemon returns every
    event overlapping the asked range; v4's Calendar and Today split them per
    local day with `state.expand_events` (all-day by Google's exclusive end;
    timed: start→24:00, whole middle days, 00:00→end, judged on the real
    length, not the 15-minute drawing floor). Each piece is a complete event
    for its day — `date`, `start`, `start_min`, `dur` are that day's part — so
    grids, labels and sorting need no special case. The whole event rides
    along as `_event_date` / `_event_start_min` / `_event_dur`; the edit
    dialog reads them through `calendar._whole()`, so editing any piece
    changes the real event. The seam (`_calendar_handler`) hands events over
    whole: ui_v3 files them by `date` and would draw every piece on day 1.
  - **Declined / free events (#125):** `_norm_event` derives `declined`
    (True when the user's own `response_status` == "declined"; checked from
    the self-attendee first) and `free` (True when `transparency` ==
    "transparent"). Declined events are muted in all grid views (EventBlock,
    agenda_row, MonthCell dot and label, Today all-day strip) using the
    "muted" theme token only — never inline colours. Today's Up Next and
    countdown exclude declined events entirely.
  - **Out-of-sync-window empty state (#116):** `_sync_window` is stored
    from the daemon's `window` field. Day and week views outside it show
    "Outside what Lumen keeps — Lumen keeps your calendar from Sep 11 to
    Dec 10" with Go to today, instead of "Nothing scheduled".
    `ask_context()` also reports that the range isn't synced. Month view
    shows whatever is cached (no out-of-window block). Fetching those days
    on demand is open product question #191.
  - **Sync status (#117):** `last_sync`, `sync_failed`, `sync_paused` are
    forwarded through `_calendar_handler` and stored in `CalendarScreen`.
    Only a `calendar.refresh` reply carries the two flags (always both); a
    cache read sends None and the screen keeps the last word, so a failure
    doesn't vanish on navigation. `_sync_status_text()` returns a compact
    status line ("Updated 3 min ago", "Couldn't reach Google Calendar —
    showing events as of 9:41", "Sync paused"). It appears in the Calendar
    header subtitle, replacing the normal subtitle only when non-empty.
    Recomputed on show/refresh, never polled.
  - **Midnight (#115):** `CalendarScreen._roll_day()` is the only place
    `today` changes: a view on the old today moves to the new one and
    fetches its range; a view you navigated to stays put.
  - **New event (#99):** times read like the edit dialog's
    (`overlays.parse_clock`: "9:30", "930", "14:00", "2pm"); a blank End takes
    the daemon's default length; "All day" hides the times. The form stays
    open until the daemon answers: success closes it with a toast and
    refreshes Today/Calendar, a failure shows in the form with everything
    still typed, and a cancelled confirm returns to it quietly.
- **Today screen live updates (#114/#115):** `on_shown` always fires
  `refresh_todos()` so Canvas-created todos and newly-due items appear on
  every switch. The per-minute `_now_timer` (stopped in hideEvent) redraws
  only the time-bound parts — now line, greeting, sync badge and the Up next
  card (swapped in place). Never a full `rebuild()` per tick: that drops
  keyboard focus and half-done clicks in the columns. On a date change the
  tick re-runs `on_shown`, and the fetched day's answer rebuilds the page.
- **Canvas:** once a login is saved, the "Save this login?" card never opens
  on its own — not even for a captured password that differs, which the 1 Hz
  poll often half-reads (#67). With nothing saved it is offered once per typed
  login; "Not now" doesn't return on the next page. Canvas settings' Change /
  Save a login button opens the card on purpose (uNID prefilled, password
  empty, no "Never"). Paused in Settings, the badge reads "Sync paused", the
  tab doesn't sync on open, and Sync now explains the pause (with Open
  Settings) instead of "Sync failed" (#83).
  - **Cookie bursts (#128):** The in-app browser fires a cookie event on almost
    every page navigation. Only the first authenticated jar triggers an immediate
    hand-off to the daemon; subsequent changed jars are coalesced by a 300 ms
    single-shot `_cookie_send_timer`. Done sends a jar still waiting (it's the
    newest session); Disconnect / forget-browser-session drops it.
  - **Post-login refresh (#129):** `_on_connected` calls `_sync_now()` directly
    when login completes (`_login_mode` was True and the result is connected).
    No separate `_auto_sync_armed` chain needed.
  - **Calendar alerts strip (#130):** When `canvas.alerts` returns items, a strip
    appears above the assignments list with a "See changes" button. Clicking it
    expands the list inline; "Got it" marks exactly the ids shown as seen
    (never "all" — one that arrived meanwhile stays) and clears the strip. A
    failed load shows "Couldn't load the calendar changes. [Try again]" with
    no Got it. The strip is hidden when there are no unseen alerts.
  - **Sync busy settles on error (#132):** Both `canvas_reconcile_now` and
    `canvas_sync_now` pass `on_error` callbacks. Errors route through
    `_on_synced({"ok": False, "error": msg})` so the busy scrim and button
    always settle. Calendar toggle errors use `_refresh_calendar()` as the
    error path.
  - **Sync summary reports failures (#135, #126):** `_summary_line` checks
    `res["failed_courses"]`, `cal.get("failed")` and `cal.get("error")`. If a
    course didn't answer or calendar writes failed, the summary says so and
    promises a retry ("Synced, but 2 courses didn't answer — Lumen will retry
    next sync."). "Up to date" is never shown alongside a failure.
  - **Keyring calls off the GUI thread (#136):** `canvas_creds.save`,
    `.load`, `.forget`, and `.prime` are blocking keyring operations. All calls
    from the Canvas screen go through `_run_keyring(fn, done)`, which runs `fn`
    on a daemon thread and delivers the result to `done` via the module-level
    `_KeyringBus` signal (queued connection — the Qt signal crosses threads
    correctly even without an event loop on the worker). Reads go through
    `_preload_creds` (one at a time; `_reset_autofill` bumps `_creds_gen` so a
    stale answer is dropped). A sign-in form or a Fill press that comes before
    the answer parks in `_fill_waiting` and runs when it lands — nothing
    reads the keyring on the GUI thread, not even `_saved_creds()`. A save
    updates the cached login; a failed forget re-probes and says so.
    `_maybe_save_login` uses only cached answers, and "not known yet" counts
    as saved: the #67 offer must never come from a guess.
- **Settings:** all three answer modes are explained; Canvas Connect starts
  the sign-in from Accounts; Advanced shows only keys the daemon reports.
- **Model naming:** plain words ("On this computer", "Claude · Haiku"); the
  raw model tag lives in tooltips.

## Invariants (tested in `tests/ui/*_v4.py`)
- Every outside write goes through the daemon's confirm gate or the window
  ConfirmOverlay. Approve only starts the write: a success toast waits for
  the daemon's answer (the screen shows it from the reply). Only sample
  mode's demo confirms carry a `toast` key, shown by the window on approve.
- A confirm can appear mid-keystroke (#80): it focuses its card, never a
  button, so Space/Enter approve nothing, and Approve ignores clicks for
  `ConfirmOverlay.ARM_MS` after the card or the window appears.
- An exception escaping a slot is logged to `ui.log` and toasted; it must
  never abort the process (`ui_v4/app.py::_excepthook`, #85).
- A daemon error or dropped connection must settle any waiting UI:
  `DaemonClient.request(..., on_error=)`; state exposes `mail_error` /
  `todos_error`, and calendar callbacks get `{"error": ...}`. No skeleton or
  busy scrim may wait forever. A callback that raises is logged, never
  allowed to strand the other waiting callers.
- Power: hidden screens defer rebuilds; QtWebEngine is imported only when
  Canvas first builds its view; the Canvas page is Frozen when hidden and
  Discarded on Done; no timers run on hidden screens. Files skips its
  folder re-read while a narrow editor hides the folder pane, and watches
  its open file through inotify — its settle timer is single-shot, started
  only by a change, never a poll.
- Text size and reduced motion: `theme.text_size()` is applied by
  `ThemeManager.apply` (every stylesheet apply); `theme.reduced_motion()`
  also follows the desktop's animation setting, asked once at startup on a
  worker thread (`prime_reduced_motion`) — never on the GUI thread; motion
  stays on until it answers.
- Tests must isolate QSettings via `tests/ui/v4_support.py::v4_settings`
  (it is Josh's real preference store otherwise).
