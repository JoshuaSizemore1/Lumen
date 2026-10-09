# Design spec — todo-fixes round 6 (2026-08-30)

Five work items, each a cluster of related open entries from `todo-fixes`.
Open at the time of writing: 30, 40, 45–58, 60–66, plus **#44 reopened
2026-08-30** (Canvas login autofill: confirmed broken on the real form). This
spec covers 22 of them; #30 and #40 are deliberately deferred (see the tail).

Each item below is: **what you see → what is actually wrong (with evidence)
→ the fix → how it gets verified.** Nothing here is a guess dressed as a
cause; where the cause is still a hypothesis it says so and the first step
is a measurement, not a patch.

---

## Item A — "The UI fights me when I click fast"
Covers **#60, #64, #49, #52, #53, #50** (+ **#48**, same class)

### What you see
Spam-clicking through emails lags badly (#60). Swapping between a label and
the inbox is slow (#64). Clicking through before things load sends you
backwards in the list and you have to scroll down again (#49). The
suggested-labels approval popup is slow (#52, #53). You want ↑/↓ to move
through emails instead of scrolling the bar (#50). Editing a todo to add a
tag made the item disappear from the list (#48).

### What is actually wrong

**1. The daemon answers the UI strictly one request at a time.**
`IPCServer._handle` (`lumen/daemon/ipc_server.py:46-57`) reads one line,
fully drains that request's response generator, and only then reads the next
line. The UI has one socket. So every request the UI makes queues behind
whatever is currently in flight.

**2. And two of the requests a single email click makes hit Gmail over the
network.** Clicking a message fires `emails.get` and `emails.auto_read`
(`ui_v2/state.py:679-686`). `emails.get` calls
`await self._mail.fetch_html(...)` when the body was never mirrored
(`daemon/router.py:1632-1636`), and `emails.auto_read` calls
`self._mail.mark_read(...)` (`router.py:1657-1661`). Both are Gmail round
trips, both on the one serialized handler.

Five fast clicks therefore queue up to **ten serialized Gmail round trips**,
and every unrelated request — the label list, the Canvas status pill, the
settings snapshot — waits behind all of them. That is #60 and #64 exactly,
and it is why the suggest-review popup is slow (#52/#53): it pulls each
message body through the same `emails.get` on the same queue
(`state.fetch_mail`).

**3. Nothing cancels or sequences stale replies.** `_set_mails`
(`ui_v2/state.py:704`) accepts whatever answer arrives, whenever it arrives.
Click into a label, click back to the inbox, and the label's late
`emails.list` reply overwrites the inbox list, re-picks `selected_mail`, and
the list's membership changes — which makes `MailScreen.rebuild` treat it as
a real change and reset the scroll to the top
(`ui_v3/screens/mail.py:259-263`). That is #49's "sends me backwards".

**4. `clear_layout` briefly turns every removed widget into a top-level
window.** `ui_v3/widgets.py:91-97` does `w.setParent(None)` then
`w.deleteLater()`. Detaching a widget from its parent makes it a top-level
widget until the deferred delete runs on the next event-loop tick. It is
used in 17 modules; a mail rebuild does it ~50 times. This is both a real
per-rebuild cost and the prime suspect for Item B's phantom windows.

**5. #48 is the same "the row left the view" class.** `TodosScreen` filters
the visible list by `self.tag_filter` (`ui_v3/screens/todos.py:132-133`) and
`TodoStore.update` lower-cases tags (`connectors/todos.py:79`). Edit a
todo's tags while a tag filter is active and the row legitimately leaves the
filtered view — indistinguishable from deletion. Nothing was lost; the UI
just never said so.

### The fix
- **Concurrency in the IPC server.** Dispatch each request line as its own
  task instead of awaiting it inline, keeping the per-request `id` for
  correlation (the client already routes replies by id —
  `ui_v2/daemon_client.py:94-97`). Ordering was never guaranteed to the UI
  anyway. Streaming chat turns stay serialized against each other so two
  model calls can't overlap (the power budget), but a body fetch must never
  block a list read.
- **Take the network out of the click path.** `emails.auto_read` returns
  immediately and marks read in the background; `emails.get` returns the
  mirrored row at once and streams the HTML backfill as a follow-up rather
  than holding the reply open.
- **An epoch guard on list replies.** `_set_mails` ignores any answer older
  than the newest scope/search request, so a late reply can never re-write
  the list you are looking at.
- **Scroll survives a rebuild.** Extend the existing keep-scroll rule
  (`mail.py:257-263`) so a rebuild that merely *adds or drops* rows keeps the
  anchor row in view instead of jumping to the top.
- **Prefetch the neighbours.** When a message is selected, prefetch the body
  of the one above and below. Arrow-key browsing then feels instant.
- **↑/↓ browse the list (#50).** Arrow keys move the selection in the message
  list, with the list scrolling to keep the selection visible. **Behaviour
  call, assumed:** arrow-key browsing does *not* mark messages read —
  Enter/click does. The current auto-read-on-select was written when "every
  selection here is a deliberate click, there is no key-browsing to protect"
  (`ui_v2/state.py:680-683`); key browsing is exactly what breaks that. Say
  the word if you would rather it marked read like Gmail's `j`/`k` does.
- **`clear_layout` stops orphaning widgets.** Use `w.hide()` +
  `w.setParent(None)` ordering that never leaves a mapped top-level, or
  reparent to a hidden sink widget before `deleteLater`.
- **#48 gets an honest answer,** not a silent vanish: when an edit moves a
  todo out of the active filter, the filter clears (or a toast says "moved —
  no longer matches #tag"). Tag case is preserved for display.

### Verification
- Unit: an epoch-guard test (late reply ignored), a scroll-anchor test, an
  arrow-key selection test, a todos test that an edit under an active filter
  never deletes the row.
- Integration: a daemon test that a slow `emails.get` does not delay a
  concurrent `emails.list` — the direct regression test for #60/#64.
- Live: the `verify` skill — drive the daemon over its socket, click through
  ten messages as fast as the harness allows, and record wall-clock per click
  before and after.

---

## Item B — Phantom windows, the Canvas flash, and the model-off crash
Covers **#63, #61, #62, #47**

### What you see
Clicking from Canvas to Settings sometimes spawns a bunch of copies of the
application window and the graphics lag (#63) — and it has happened doing
other things too. Opening the Canvas tab the first time each session shakes
the screen / replaces it with a new-looking one (#61). Lumen crashed when the
model was off and you clicked the "turn it on" link in a chat (#62). Stray
daemons need a way to be killed (#47).

### What is actually wrong

**#63 — two candidate causes, both real code, both need one measurement to
separate.**
- *Candidate 1:* `clear_layout`'s `setParent(None)` (Item A, finding 4)
  produces dozens of momentary top-level widgets per rebuild. Under a tiling
  window manager a mapped top-level gets tiled — which is precisely "copies
  of the application window", and precisely why it is "a general bug" rather
  than a Canvas-only one.
- *Candidate 2:* `LumenWindow._restyle` (`ui_v3/main.py:424-439`) builds an
  entirely **new** `LumenWindow`, shows it, and closes the old one. If the old
  window is kept alive by anything — and a built `CanvasScreen` holds a
  `QWebEngineProfile` and a Chromium render process
  (`screens/canvas.py:881-891`) — the "closed" window may not actually go
  away. That also explains why Canvas is the tab it is noticed after.

  First step is a measurement, not a patch: log every top-level widget
  (`QApplication.topLevelWidgets()`) on a timer and reproduce. The log says
  which candidate it is before anything gets changed.

**#61 — the Canvas tab is built synchronously on first click.**
`switch_to` calls `self._screen(key)` before `setCurrentIndex`
(`main.py:246-249`), and `_screen` swaps a placeholder out of the
`QStackedWidget` (`main.py:252-271`). `CanvasScreen` is the largest screen in
the app (1441 lines) and its `showEvent` immediately fires four daemon
requests. The visible placeholder→real-widget swap plus the blocking build is
the "shake / replaced with a new one".

**#62 — a widget is very likely being destroyed inside its own click
handler.** `ModelOffNotice` is a `ClickRow`; its `mousePressEvent` calls
`self._on_click()` and *then* `super().mousePressEvent(ev)`
(`widgets.py:601-604`). `_on_click` here emits `view_requested("settings")`
and `model_switch_highlight_requested` (`widgets.py:652-654`), which switches
screens and triggers rebuilds — and a rebuild runs `clear_layout`, which
detaches and schedules deletion of the very notice still on the call stack.
Detaching a widget mid-event, with PyQt then owning its lifetime, is a known
hard-crash shape.

### The fix
- Measure #63 first (top-level widget census on a timer), then fix the cause
  the log names. Fix `clear_layout` regardless — it is wrong either way.
  If `_restyle` is implicated, replace the window swap with an in-place
  re-polish, or make the teardown provably complete (explicitly tear down the
  Canvas web view and its profile before closing the old window).
- **#61:** build `CanvasScreen`'s heavy content after the switch paints —
  the tab appears immediately with its chrome, content fills in — and stop
  the placeholder swap from being visible.
- **#62:** make every `ClickRow`/`ClickLabel` callback fire on the next event
  loop tick (`QTimer.singleShot(0, ...)`) so a handler can never delete the
  widget it is running inside. One change, covers every clickable row in the
  app, not just this one.
- **#47:** a `lumen --kill` / `lumen-daemon --stop` command plus an in-app
  Quit-everything action (Settings, and a shortcut) that finds Lumen daemon
  processes by their socket path and stops them cleanly.

### Verification
- #63: the widget census log, then re-run the repro until it stays at one
  top-level window through 50 Canvas↔Settings switches.
- #62: a headless test that clicks `ModelOffNotice` while the chat rebuilds,
  asserting the process survives (this crashes today).
- #61: offscreen timing — first Canvas paint under a threshold, and the tab
  chrome present on the first painted frame.
- #47: start two daemons, run the kill path, assert both sockets are gone.

---

## Item C — Email labels: stop guessing, and let Lumen un-label
Covers **#51, #46, #55, #56**

### What you see
Auto-categorise once labelled everything FIDELITY (#46). You want a keyword
dictionary built from mail already in each label to do the easy ones, with
the local model only for the uncertain ones (#51). Lumen says it cannot
*remove* labels, only add them (#55). Accepting suggestions should apply them
programmatically, not by asking the AI to use a tool (#56).

### What is actually wrong

**#55 is true and it is a real gap.** `label_email` is the only mail tool the
model is given (`daemon/local_tools.py:100-119`). There is no un-label tool —
even though the daemon route (`router.py:1715-1725`) and the connector method
(`connectors/email_menu.py:773`) both already exist. Classic
"nearest-capable-tool" gap: the model is not confused, it is correct.

**#56 is already done — it needs confirming, not building.**
`apply_suggestion` and `accept_all_suggestions` (`ui_v2/state.py:875-913`)
call `emails.apply_label` directly. The model is only in the *decision*, never
in the *apply*. Plan: verify it end to end, add a regression test that pins
it, and mark #56 closed rather than writing new code for it.

**#46 is a structural weakness in the classifier.** `label_suggest.suggest`
(`daemon/llm/label_suggest.py:71-83`) asks the 4B model one message at a time
against label descriptions derived from filed mail. A label with lots of
filed mail gets a rich, concrete description; a label with none gets literally
`"nothing filed here yet"`. The model anchors on the vivid one. Nothing
detects a degenerate run where every message gets the same answer.

### The fix
- **A deterministic first pass (#51).** Build a per-label keyword profile from
  mail already filed under it — sender domains, sender addresses, and
  distinctive subject tokens, scored by how *exclusive* they are to that label
  (a term common to every label carries no signal). A message that clears a
  confidence threshold on exactly one label is suggested with no model call at
  all. Everything else falls through to the existing per-message model call.
  Both paths produce the same reviewable suggestion — you still accept or deny
  each one (#33 stays "won't do": nothing auto-files).
  Side benefit: a run is now mostly local SQL, which is a direct fix for the
  slow classification pass in #52.
- **A degenerate-run guard (#46).** If a pass assigns the same label to more
  than a set fraction of messages, drop the model's verdicts for that label to
  "weak" (i.e. no suggestion) and report it honestly in the review popup
  rather than filing an inbox under one bucket.
- **A real `unlabel_email` tool (#55),** with the same wording discipline as
  `label_email`, wired to the existing `remove_label`. Description explicitly
  covers "remove", "take off", "unfile".
- **Confirm and pin #56.**

### Verification
- Unit: profile-building on fixtures; exclusivity scoring; a threshold test
  that an ambiguous message falls through to the model; the degenerate-run
  guard.
- Eval: **derive the prompt surface from production, never hand-copy it** —
  run the real `mail.suggest_labels` route against a fixture mirror and
  measure precision before/after. A hand-built prompt has produced a wrong
  headline in this repo twice.
- Router: an `unlabel_email` tool test, plus a "remove the X label from this
  email" routing test.

---

## Item D — Canvas: login autofill, new classes, the slow browse, re-pulling
Covers **#44 (REOPENED), #58, #57, #65, #66**

### What you see
**Canvas never attempts to fill in the username and password, and it does not
autofill the way Google Passwords would (#44, reopened 2026-08-30).** Auto-sync
is not showing new classes (#58). "Browse Canvas" opened a white screen on
`utah.instructure.com` and took a very long time (#57). Dismissing an
assignment or announcement jumps the view back to the top of the list (#65).
You want a button that pulls from the sync and creates/updates todos and
calendar events, including when things were removed from Canvas (#66).

> **#44 was marked fixed on 2026-08-25 with the caveat "still needs one live run
> on the real CAS + Duo form — only Josh can drive that." That live run has now
> happened and it does not work. The entry is reopened; the unit evidence that
> closed it was never sufficient.**

### What is actually wrong

#### #44a — nothing is ever saved, so there is nothing to fill

The keyring is almost certainly empty, and every path that would tell you so is
silent. Four things have to go right in a row and at least two of them do not:

- **The password is sampled once a second.** `_start_cred_poll`
  (`ui_v3/screens/canvas.py:951-960`) runs a 1 Hz timer that reads whatever is
  currently typed into the form. Type a password and press Enter inside that
  second — or paste it — and the field is never read.
- **Saving needs both halves or it silently does nothing.**
  `_maybe_save_login` (`canvas.py:992-1005`) returns without a word when either
  the uNID or the password is missing. There is no log line on that path at
  all, which is why this has never shown up in a log.
- **Cross-origin frames cannot be read.** The frame walk in `canvas_login.py`
  counts a cross-origin frame and moves on (`_DOCS_JS`, `__blocked`). If the
  U of U password step or the Duo prompt is an iframe from another origin, both
  capture and fill are blind to it by construction.
- **The end result is the wrong message.** With nothing saved,
  `_inject_autofill` (`canvas.py:1356-1371`) logs `"no saved login — user types
  it"` and returns. On screen that is indistinguishable from "the autofill is
  broken" — and the Remember switch is on by default (`canvas.py:251`), so it
  looks like it should have worked.

#### #44b — autofill never runs when you press "Browse Canvas"

`_inject_autofill` is gated on `_login_mode` (`canvas.py:1344-1346`), and
`_login_mode` is only ever set by **Connect** (`_start_login`, `canvas.py:916-922`).
`_start_browse` (`canvas.py:924-925`) opens with `login=False`. So if Browse
lands you on the CAS form — which is exactly what an expired session does, and
what #57's white screen suggests — Lumen will not even try to fill it.

#### #44c — the fill is blind, on a 6-second fuse, and stops at the first match

- The retry budget is 6 s from `loadFinished` (`RETRY_DEADLINE_MS`,
  `canvas_login.py:52`). #57 says the page can take far longer than that.
- `attempt()` fills the **first** password field it finds and immediately calls
  `finish()` (`canvas_login.py`, `attempt`/`tick`). A hidden or decoy password
  input on step 1 of a two-page CAS flow consumes the whole budget on the wrong
  field.
- All of it happens invisibly. When it misses there is no affordance to retry,
  and nothing on screen says a saved login exists.

#### #44d — the real gap: QtWebEngine has no password manager

This is the honest answer to "it does not autofill like Google Passwords
would." Qt's web engine ships **no** credential store, no save-password bar, no
fill-on-focus dropdown. Everything above is Lumen hand-rolling a blind
substitute for it, and a blind substitute is exactly what keeps failing. The
fix is not a cleverer injection — it is building the *visible* equivalent.

**#65 is confirmed and is the mail-scroll bug again.** `_dismiss` →
`_set_dismissed` → `_refresh_content()` (`screens/canvas.py:746-766`), and
`_render_assignments` opens with `clear_layout(self._assign_box)`
(`canvas.py:457-458`). The whole list is rebuilt, so the scroll area resets.
Josh's Find #1 was this exact bug in Mail and it was fixed there — the fix
was never generalised.

**#58 is *not* the archive flag.** `included` defaults to 1 in the schema and
in the migration (`daemon/db.py:145,285`), so a newly discovered course is
included by default. The likelier cause is upstream: `CanvasClient.courses`
asks Canvas for `enrollment_state=active` only
(`connectors/canvas_client.py:81-87`). A next-term course whose term has not
started, or whose enrollment is still invited/pending, is not in that set —
and unpublished courses are filtered out by Canvas regardless. **This needs
one measurement against the real account before anything is changed:** a
debug route that dumps what `/api/v1/courses` returns for each
`enrollment_state`, so the real reason is on the record. Then either widen the
query or surface the courses Canvas is holding back, with an explanation.

**#57 is unmeasured.** Plausible causes are the first paint of a cold
`QWebEngineProfile`, or the login/session handshake. Instrument
`_open_in_browser` → `loadFinished` with timings first
(`canvas.py:906-918`); a loading indicator in the browser chrome is worth
adding either way, so the wait is legible rather than a white void.

**#66 does not exist yet as a user-facing action.** The pieces do:
`CanvasSync.sync_once` reconciles todos (`canvas_reconcile.reconcile_todos`),
writes calendar events through the marker writer, and queues disappearances
for review (`connectors/canvas_sync.py:145-185`). The Canvas tab has
"Sync now", which pulls the mirror, but the reconcile/calendar effects and
particularly the *removals* queue are not surfaced as one deliberate
"reconcile everything now, show me what changed" action.

### The fix

**#44 — rebuild it as a visible password manager, not a blind injection.**
- **Fill on focus, not on page load.** When a username or password field takes
  focus and a saved Canvas login exists, fill it. This is what Chrome actually
  does, and it sidesteps every load-timing and deadline problem at once — a
  form that mounts after 30 seconds still works.
- **A "Fill login" button in the browser toolbar,** enabled whenever a saved
  login exists. User-triggered, always available, and visible proof that Lumen
  has a login stored — replacing a silent injection whose only failure mode was
  "nothing happened".
- **Autofill works on Browse too,** not just Connect: the gate becomes "this
  page is a login form", not "the user arrived via the Connect button".
- **A Chrome-style "Save login?" prompt (Josh's design, 2026-08-30).** The
  moment a login succeeds, a small card appears in the top-right of the Canvas
  browser chrome — where Chrome puts it — carrying:

  | | |
  |---|---|
  | **Username** | editable text field, prefilled from capture |
  | **Password** | editable, masked, with a reveal (eye) toggle, prefilled from capture |
  | **Actions** | **Save** · **Not now** · **Never for Canvas** |

  This is the important structural change, not just a nicer UI: **the fields are
  editable, so a failed capture is no longer fatal.** If the sampler missed the
  password, or it was typed inside a cross-origin Duo frame Lumen cannot read,
  the box is simply empty and you type it — instead of today's silent nothing
  that never recovers. Capture drops from "the only way credentials are ever
  stored" to "a prefill convenience."

  - Trigger: the confirmed-login moment that already exists — the Canvas session
    cookie handoff (`canvas.py:1318`), which is precisely Chrome's trigger
    (a successful navigation after a form submit).
  - If a login is already saved and what you just used differs, the card reads
    **"Update saved password?"** — same fields, same edit affordance.
  - **Never for Canvas** is remembered, so it stops asking. Settings still shows
    the saved/not-saved state with a Forget button, so the decision is reversible.
  - The password lives in a masked field and goes straight to the OS keyring.
    The UI-side-only invariant holds: it never reaches the daemon or a file
    (`ui_v3/canvas_creds.py`).

- **Capture on submit, not on a 1 Hz timer** — hook the form's own submit and
  read the field at that instant. This now only decides how well the popup is
  *prefilled*, not whether anything is saved at all.
- **The "Remember this login" switch goes away** (`canvas.py:251, 629-630`).
  A pre-checked switch that silently saved nothing is exactly the affordance
  that made this look like it was working; the popup replaces it and asks at
  the one moment the answer is actually known.
- **Say so when there is nothing saved.** A visible "No saved login" state in
  the login view, and a Canvas row in Settings showing saved / not saved with a
  Forget button — so "nothing is stored" can never again look like "autofill is
  broken".
- **Fill every candidate field, not the first,** and keep retrying past the
  current 6-second fuse for as long as the login view is open.
- **Cross-origin frames get named.** When the form is in a frame Lumen cannot
  reach, say that in the UI rather than failing silently — it is the one case
  where no amount of code will help, and you should be told to type it.

**The rest of Canvas:**
- **#65:** preserve scroll position across a content rebuild, and make a
  dismiss remove just that row rather than re-rendering the list.
- **#58:** the diagnostic route first; then the change it justifies (most
  likely querying additional enrollment states and listing courses Canvas
  reports as restricted-by-date, so a not-yet-started class is *visible and
  explained* rather than silently missing).
- **#57:** timings, a visible loading state in the browser chrome, and
  whatever the timings actually indict.
- **#66:** a "Reconcile now" action in the Canvas tab that runs a full pass
  (pull → todos → calendar) and then shows a summary sheet: created, updated,
  and *proposed removals* — the last of which you approve item by item,
  consistent with how the background path already behaves (it cannot raise a
  modal, so it queues).

### Verification
- Unit: scroll preservation; a reconcile-summary test covering create,
  update, and removal-proposal counts.
- #44: extend the existing node stub-DOM tests (`tests/ui/js/`) to cover
  focus-triggered fill, a decoy hidden password field, and a form that mounts
  after the old 6-second deadline. Plus a Qt test that the Fill button is
  enabled exactly when a credential is stored.
- #44 save-prompt tests: the card appears on a confirmed login; **a login whose
  password capture returned nothing still produces a usable card with an empty,
  editable password field** (the regression test for today's whole failure
  mode); Save writes the *edited* values, not the captured ones; Never suppresses
  the card and Settings can undo that; an existing-but-different credential
  shows the update wording.
- **#44 is not closeable on unit evidence.** It was closed that way once and it
  was wrong. It closes when Josh runs the real CAS + Duo login and reports the
  fill landing — and the new visible states (saved / not saved / Fill) mean a
  failure will finally say which of the four things went wrong.
- Live (needs the real account, so this one is yours to drive): the courses
  diagnostic, and a reconcile pass after adding an assignment in Canvas.

---

## Item E — Never "(no answer)", and a screen that means something
Covers **#45, #54**

### What you see
Lumen sometimes just says "(no answer)" (#45). Asking something while on the
Mail screen should be understood as being about email (#54).

### What is actually wrong

**#45 is found, and it is two lines.** In
`LumenClient.chat_with_tools` (`daemon/llm/client.py:110-116`), when the model
returns no tool call the loop yields `msg.get("content", "")` and returns —
**including when that content is empty**. The router forwards it as a chunk
(`router.py:3023-3024`), the ask bar accumulates nothing, and `_on_done`
prints "(no answer)" (`ui_v3/askbar.py:245-250`). A 4B model returning an
empty final message after a tool round is common — and Lumen currently reports
that as if it had nothing to say, discarding tool results it already has in
hand. Nothing is logged when it happens, which is why it has stayed
mysterious.

**#54:** the ask bar already sends `{"screen": ...}` context (each screen's
`context()`), and the Files case was wired up in #43. What is missing is
using the screen as a *routing prior*: on the Mail screen a bare "label this
as Work" or "reply to her" should bias to the mail tool group instead of
falling through the generic keyword hints.

### The fix
- **A terminal-answer guarantee.** When a tool-loop turn ends with empty
  content: (1) log it with the conversation's tool calls, so it stops being
  invisible; (2) retry the final round once, without tools, asking the model
  to answer from the tool results already in the transcript; (3) if it is
  still empty, synthesize an honest answer from the tool results themselves
  ("I looked up X and found: …") rather than "(no answer)". The string
  "(no answer)" should become unreachable except when the daemon truly never
  responded — and in that case it should say *that*.
- **Screen as a routing prior (#54).** The active screen adds weight to its
  own tool group in the router's group selection, without hard-overriding an
  explicit subject ("add milk to my todos" on the Mail screen is still a
  todo). Each screen's `context()` already carries what is open, so the prior
  is per-screen: Mail → mail group, Calendar → calendar, Todos → todos,
  Canvas → Canvas, Files → filesystem (already partly done in #43).

### Verification
- Router tests: an empty-content final round produces real text, never an
  empty chunk; a tool-loop turn whose model output is empty still reports
  what the tools found.
- Routing tests: the same ambiguous prompt attaches different tool groups on
  different screens, and an explicit subject still wins over the prior.
- Live: the `verify` skill against the real daemon on prompts that have
  produced "(no answer)".

---

## Deliberately not in this round

- **#30** (the Anthropic receipt renders badly) — needs that specific message
  as a sample, and Josh's Find #3 already established that HTML fidelity is
  bounded by `QTextBrowser`'s HTML4/CSS2 subset. A real fix is a QWebEngine
  reading pane, which is its own piece of work, not a bug fix.
- **#40** (starred email support) — a feature, not a repair: Gmail STARRED
  handling, a star control in the list and pane, and a Starred mailbox. It
  belongs in `new-features.md`, and it is cheap to do once Item A has made the
  mail screen fast.

## Sequencing

**A → B → C → D → E** is the order that pays off soonest. A and B share the
`clear_layout` fix and the "an action must not make the row vanish" rule, so
they want to be adjacent. C depends on nothing. D's two live measurements
(#58, #57) can only be driven by Josh, so its diagnostics should land early
even if its fixes land last. E is self-contained.

Test suite is currently 1526 passed / 19 skipped; every item above adds
regression tests, and nothing here should reduce that number.
