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
- **Canvas:** once a login is saved, the "Save this login?" card never opens
  on its own — not even for a captured password that differs, which the 1 Hz
  poll often half-reads (#67). With nothing saved it is offered once per typed
  login; "Not now" doesn't return on the next page. Canvas settings' Change /
  Save a login button opens the card on purpose (uNID prefilled, password
  empty, no "Never"). Paused in Settings, the badge reads "Sync paused", the
  tab doesn't sync on open, and Sync now explains the pause (with Open
  Settings) instead of "Sync failed" (#83).
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
