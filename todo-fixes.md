# Todo Fixes for Lumen

The active bug and friction backlog, started 2026-10-11. The previous list is
`archived-fixes.md`.

- **Numbering continues from the archive** (its "Josh's Finds" list ended at #71),
  so a "todo-fixes #N" in a code comment, test or skill points at exactly one
  entry: #71 and below are in the archive, #72 and up are here.
- **#44 and #71** were still open in the archive and are carried over below.
- **#72–#165** are items 1–94 of the 2026-10-11 full review, in the same order
  (review item N is entry N + 71). Full evidence and the "checked and found
  clean" coverage list are in `.claude/review-2026-10-11.md`.
- **#166–#179** are the review's router misfires.
- **#180–#194** are product questions. Nothing gets built for these until Josh
  answers.
- New finds go at the end under **Josh's Finds**, with the next free number.
- When an entry is done, append "— ✔ fixed (date)" to its first line and add a
  **Done:** bullet, as in the archive.

Evidence tags from the review: **[checked]** re-read against the code;
**[probe]** reproduced with a throwaway script; **[traced]** read end to end, not
reproduced; **[needs you]** depends on Josh's machine or accounts. ×2 / ×3 means
that many reviewers found it independently. Paths are relative to `lumen/`; line
numbers are as of 2026-10-11 and will drift, so re-check the code before fixing.

---

## Carried over from archived-fixes.md

44. The auto saved canvas login is not auto filling in the browser when loging in — ⧗ rebuilt 2026-08-30, awaiting Josh's live CAS + Duo run
    - Four faults were found and fixed (nothing was ever saved; autofill only ran
      after Connect; the fill stopped at the first password field on a 6 s fuse;
      QtWebEngine has no password manager). Full history in `archived-fixes.md`.
    - **Not closeable on unit evidence.** It was closed that way once and was
      wrong. Needs one live sign-in through the Canvas tab.
71. Completly redesign the TODO section — ⧗ deferred by Josh (2026-10-10, "we will do more redesigning of todo later")
    - Already decided: Canvas assignments get a short coloured course chip
      ("CS 4150", course prefix stripped from the title, the "canvas" tag
      hidden), with points / submitted / Open in Canvas in the detail; completed
      items go to a collapsed "Done (N)" section.
    - Still open: layout (list + detail pane vs single list vs board) and extras.
    - `canvas_assignments.html_url` / points / submitted aren't joined into the
      todo dict yet.
    - #187 (todo sort order) is related but separate.

---

## Review 2026-10-11 — Tier 1: fix first

Each of these shows wrong information every day, breaks a safety rule (no write
without a click), loses something typed, or makes a feature not work at all.

### Dates and times (one root cause: UTC vs local)

72. **Evening events land on the wrong day** (review #1) [checked] — ✔ fixed (2026-10-11)
    - The calendar cache buckets events by their UTC date. Anything after 6 PM
      (5 PM after Nov 1) is filed under tomorrow: missing from Today, the day
      view, the briefing and free-slot answers, and shown on the next day
      instead. Canvas 11:59 PM due-date events never appear on their own day.
      Tests only use morning times, which is why this slipped through.
    - **Where:** `daemon/connectors/gcal.py:41,54` (`date(start_at)` in SQLite
      converts to UTC).
    - **Fix:** store a local-date column at `_normalize` time and filter on it.
    - **Done:** `events.start_day` (local date, from `gcal_time.local_day`) is
      written on every `replace_window` and backfilled on start (NULL rows
      only, so a crash mid-migration heals); the window delete and `list_range`
      filter and sort on it. On a copy of the
      real DB, 9 of 29 cached events moved back to their own day (every 11:44 PM
      Canvas block, an evening dinner).
73. **Event times break across daylight saving — DST ends 2026-11-01** (review #2) [checked state.py; probe] — ✔ fixed (2026-10-11)
    - Several places take today's fixed −06:00 offset and apply it to future
      dates. After Nov 1:
      - "Dentist Nov 5 2pm" is booked at 1 PM, though the confirm says 2 PM.
      - November events show an hour off in Calendar.
      - Editing only the end time shifts the start.
      - Free slots are offered over real events.
      - A model time ending in "Z" is shown as local in the confirm but booked
        as UTC.
    - **Where:** `ui_v2/state.py:1249`, `daemon/llm/event_create.py:52-57`,
      `daemon/connectors/free_slots.py:92-99`, `daemon/router.py:666-669`,
      `daemon/llm/meeting_prep.py:61,98`.
    - **Fix:** convert per instant with `.astimezone()` or `ZoneInfo`, as
      `gcal_time.tz_offset` already does.
    - **Done:** every listed site now converts at the event's own instant
      (`.astimezone()` with no argument): the UI calendar handler, free-slot
      working hours and busy intervals, `calendar_context`, meeting prep's
      lookup and "when" line, and the event-create gate. The gate reads the
      model's time as a local wall clock and drops any offset it adds ("Z"),
      since the prompt asks for local times; the confirm shows what gets booked.
      Same class, found in review: the mail search's `on:`/`after:`/`before:`
      day bounds used today's offset too — fixed the same way.
74. **Canvas tab is a day off for evening deadlines** (review #3) [checked] — ✔ fixed (2026-10-11)
    - Homework due tonight at 11:59 PM shows "Due tomorrow"; last night's shows
      "Due today" under "Next 7 days" instead of Overdue. The Todos screen has the
      right date, so the two disagree. The Ask bar context also sends the UTC
      time unlabelled.
    - **Where:** `ui_v4/screens/canvas.py:115-175` (`due_at[:10]`), same in ui_v3.
    - **Fix:** use `canvas_events.due_local()`.
    - **Done:** new `canvas_events.due_day()` (local day, bare-date fallback) is
      used by the badge/chip, sort key, time sections and "not turned in" check
      in both v3 and v4. The v4 Ask bar context sends the local due time. On the
      real DB, 63 of 106 assignments changed day.
75. **Mail times reach the model in UTC, labelled as local** (review #4) [probe ×3] — ✔ fixed (2026-10-11)
    - A 9 PM email reads as "03:15 tomorrow" in the briefing. The commitment scan
      is told an evening email was "sent on Friday" when it was Thursday, so
      "I'll send it tomorrow" lands a day late. Same in triage and meeting prep.
      `search_email` shows the correct local time, so the model sees two
      different times for the same email.
    - **Where:** `daemon/router.py:707`, `daemon/llm/briefing.py:76`,
      `daemon/llm/commitments.py:80`, `daemon/llm/triage.py:36,133,178`,
      `daemon/llm/meeting_prep.py:136,229`.
    - **Fix:** `.astimezone()` before formatting.
    - **Done:** one helper, `email_menu.local_received()`, used by
      `mail_context`, the briefing, the commitment scan (which now also gets the
      weekday: "Sent on 2026-10-08 (Thursday)"), both triage lines and meeting
      prep. On the real mirror, 174 of the 716 newest emails had been dated a
      day off.
76. **Ordinary questions get no current date** (review #5) [probe] — ✔ fixed (2026-10-11)
    - For "did Chris email me yesterday?" or "how many days until Christmas?" the
      prompt contains no date at all. Claude mode loses the CLI's own date line
      too, because `--system-prompt-file` replaces it.
    - **Where:** `daemon/router.py:436`, `:947-1004`.
    - **Fix:** add "Today is <date> (<weekday>) <time>" after the identity/memory
      block, so the warm prefix cache still holds.
    - **Done:** `router.date_line()` ("Today is Sunday 2026-10-11 (local
      timezone UTC-06:00).") rides in every `_build_messages` prompt, right
      after identity + memory. A test pins that the warm prefix is still a
      string prefix of the real system message.
    - **No clock time, on purpose:** the line sits ahead of the history, so a
      time that changes every minute would throw away the history's prompt
      cache on nearly every turn (power/thermal rule). Calendar questions
      already get the clock from `calendar_context`'s "Now:" line.

### Safety rules (writes without a click, or the wrong thing deleted)

77. **The model can run tools it was never offered** (review #6) [checked; whether the 4B would actually emit one is untested] — ✔ fixed (2026-10-11)
    - In Local mode the tool executor runs any tool name the model emits,
      including `create_event`, `update_event` and `delete_event`, which are kept
      out of the offered list on purpose. A slip by the 4B would write to Google
      Calendar with no confirm. Claude mode is safe because its schema limits the
      names.
    - **Where:** `daemon/router.py:3490-3534`.
    - **Fix:** reject any tool name not offered this turn.
    - **Done:** the chat tool executor builds the set of names offered this
      turn and answers anything else with "tool error: there is no tool named
      … here" (logged to the tool log as a failed call; the bridge is never
      reached). The book-recommendation loop had the same hole and the same
      guard.
78. **Mail rules match keywords inside other words** (review #7) [checked] — ✔ fixed (2026-10-11)
    - The rule author is also told to emit "generous" keyword lists. The prompt's
      own BSA example matched three unrelated messages, which would be moved out
      of the inbox in Gmail on every sync: a Wells Fargo statement ("bsa" inside a
      URL), a LinkedIn "Absa Group" email, and Mom's "troopers closed I-15".
    - **Where:** `daemon/connectors/mail_rules.py:31-40`,
      `daemon/llm/rule_author.py:17-24`.
    - **Fix:** word-boundary matching, ignore URLs, and show sample matches in the
      rule confirm.
    - **Done:** `mail_rules.keyword_hit()` matches whole words/phrases
      (case-insensitive, a plural "s"/"es" still counts, flexible spacing inside
      a phrase) on text with links removed. All three examples above now miss;
      "BSA camp-out" and "Troops 12 and 40" still hit. The rule author no
      longer asks for "generous" lists: it's told keywords match whole words
      and every match leaves the inbox. The Create-mail-rule confirm gains a
      "For example" row with up to 3 matching inbox emails (sender — subject).
79. **"don't forget" deletes memories** (review #8) [checked order; probe] — ✔ fixed (2026-10-11)
    - "add a todo: don't forget to call mom" goes to the memory-forget route,
      which runs before todo-add. It deletes every memory bullet and log row about
      "mom", asks for no confirmation, and adds no todo.
    - **Where:** `daemon/router.py:381-383`, `:2307`.
    - **Fix:** require an explicit memory target ("forget what you know
      about…"), exclude "don't / not forget", and check todo-add first.
    - **Done:** the forget route now needs a request aimed at Lumen's memory:
      "forget" leading the message (after "please", "Lumen,", "can you", …)
      but not "forget to …", or "stop remembering" / "delete what you know" /
      "forget everything you know". "forget about PulteGroup" and "Forget that
      I like tea" still work. Todo-add ("add a todo:", "remind me to") is
      checked first.
80. **A confirm that pops up while you type can be approved by Space** (review #9) [checked; probe] — ✔ fixed (2026-10-11)
    - Non-destructive confirms (Create event, Allow file write, Save rule, Add to
      calendar) put focus on Approve. If one arrives while typing in the Ask bar,
      the next Space approves it. Same if the window was hidden and gets re-shown.
    - **Where:** `ui_v4/overlays.py:253-255`.
    - **Fix:** focus the card itself and ignore clicks for ~500 ms after it opens.
    - **Done:** focus goes to the card, so Space/Enter press nothing; Tab
      reaches Cancel first. Approve ignores clicks for 500 ms after the card
      opens, and again after the window is re-shown from the tray. (Not on
      mere re-activation: that silently ate a deliberate first click on a
      visible, unfocused window.) v4 only; the v3 fallback's overlay still
      focuses Approve.
81. **`lumen --quit` can kill unrelated programs** (review #10) [checked] — ✔ fixed (2026-10-11)
    - Any process whose first argument starts with "lumen" counts as Lumen.
      `nvim lumen-notes.md`, `less lumen.log` and `grep lumen file` would get
      SIGTERM then SIGKILL, so an editor with unsaved work could lose it.
    - **Where:** `instance_lock.py:54-62`.
    - **Fix:** match only the real console scripts or `-m lumen.*`.
    - **Done:** `instance_lock._program()` reads what a process runs off the
      command itself: an executable named exactly like one of the pyproject
      console scripts, or a Python interpreter handed one of those scripts or
      `-m lumen.*`. Another program's arguments never count (a `git commit -m
      "lumen.…"` was also a match before). A test pins `CONSOLE_SCRIPTS` to
      pyproject's `[project.scripts]`.
82. **Gmail "Disabled" doesn't stop Gmail sync** (review #11) [checked] — ✔ fixed (2026-10-11)
    - `mail.refresh` still syncs and runs mail rules (which write to Gmail) on
      launch, on each Mail visit, and on ↻. `calendar.refresh` has the guard, with
      a comment about this exact bug.
    - **Where:** `daemon/router.py:1892-1898`.
    - **Fix:** the same guard `calendar.refresh` has.
    - **Done:** `Router._enabled(connection)` (was `_calendar_enabled`) guards
      both refreshes. Disabled, `mail.refresh` returns the mirror with no
      network.
83. **Canvas "Pause sync" only pauses the timer** (review #12) [probe] — ✔ fixed (2026-10-11)
    - Opening the Canvas tab (mirror older than 5 minutes), Sync now, and the
      sign-in hand-off all still sync, including calendar writes and model calls.
      Settings says "Paused" while the tab says "Synced just now". Pausing Google
      Calendar doesn't stop Canvas→Calendar writes either.
    - **Where:** `daemon/connectors/canvas_sync.py:127,206,239`,
      `daemon/router.py:879,1307,1576-1594`.
    - **Done:** `CanvasSync.sync_once` refuses while paused, which covers
      every caller. `canvas.sync_now` / `canvas.reconcile_now` answer `{ok:
      false, reason: "paused"}` and `canvas.status` carries `paused`. The v4
      tab shows "Sync paused", skips its open-time sync, and Sync now explains
      the pause with an Open Settings button instead of "Sync failed". The v3
      pill says "sync paused". With Google Calendar paused, Canvas still
      mirrors and reconciles todos but writes nothing to the calendar
      (`CanvasSync.calendar_paused`, wired to the calendar's switch); it
      catches up on resume.
84. **Mail keyword pre-sort treats gmail.com as one sender** (review #13) [probe] — ✔ fixed (2026-10-11)
    - If BSA leaders write from Gmail, then Mom's and recruiters' Gmail mail gets
      suggested as BSA, and "Accept all" files it away.
    - **Where:** `daemon/llm/label_keywords.py:56-61,129-135`.
    - **Fix:** ignore free-mail domains (gmail.com, outlook.com, …) as a sender
      domain signal.
    - **Done:** `label_keywords.FREE_MAIL` (Gmail, Outlook/Hotmail/Live, Yahoo,
      iCloud, AOL, Proton, GMX, Zoho, Fastmail, HEY, Yandex and the big US ISP
      domains) contributes no `domain:` term. The exact address still counts,
      so mail from a known leader's Gmail address is still evidence.
85. **Any error in a UI click handler crashes all of Lumen** (review #14) [checked; probe] — ✔ fixed (2026-10-11)
    - PyQt 6.11 aborts the process on an unhandled exception in a slot, and in
      `lumen` mode the daemon dies with it. No `sys.excepthook` is installed, and
      when launched by Super+L stderr goes nowhere. Any latent screen bug is a
      full crash with no log.
    - **Where:** `ui_v4/app.py`.
    - **Fix:** an excepthook that logs to `~/.local/state/lumen/ui.log` and shows
      a toast.
    - **Done:** `ui_v4/app.py` installs `_excepthook` at startup: it logs the
      traceback to ui.log and stderr, and toasts "Something went wrong
      there. Lumen kept running; details are in ui.log." Ctrl+C still quits. The UI
      process now has a log at all (`setup_logging`: rotating
      `$XDG_STATE_HOME/lumen/ui.log`, 1 MB × 2, INFO and up for `lumen.*`),
      so the Canvas sign-in diagnostics (#44) finally land somewhere. The v3
      fallback (`lumen-ui-v3`) doesn't get the hook.

### Losing what you typed or saved

86. **Files: Save can overwrite newer content on disk** (review #15) [checked; probe]
    - An open file is never re-read. If the Ask bar's file tool edits it, or the
      distiller rewrites memory.md while it's open, the editor keeps the old
      text, and Save (even with no changes) writes the old text back.
    - **Where:** `ui_v4/screens/files.py:224-232,304-305,654-666`.
    - **Fix:** remember the file's modified time at open; reload if unchanged
      locally, ask "reload or keep mine" if not; check again on Save.
87. **Files: "Suggest an edit" is applied blind** (review #16) [checked; probe]
    - No diff is shown (v2 had one, and file-workbench.md calls the diff "the
      confirmation"). Apply replaces the whole buffer, wipes anything typed while
      the model was drafting, and clears undo.
    - A truncated model reply becomes a truncated file on the next Save.
    - A file that starts with a code block gets cut down to the block's contents
      by the fence-unwrapping step.
    - **Where:** `ui_v4/screens/files.py:608-626,739-750`,
      `daemon/llm/file_edit.py:50-52,71`, `daemon/llm/file_write.py:130-132`.
88. **Files: unsaved edits and "kept drafts" vanish on quit** (review #17) [checked]
    - The close button quits the app in `lumen` mode, and nothing checks for
      unsaved work, despite the toast promising "They're back when you open it
      again".
    - **Where:** `ui_v4/main.py:691-695`.
    - **Fix:** Save / Discard / Cancel on quit, or persist drafts.
89. **memory.md loses whole sections past 4,000 characters** (review #18) [checked; probe]
    - `load()` truncates at the cap, and every distill or forget run writes the
      truncated text back. A 5,369-char test file lost a whole section plus some
      bullets. Hand edits beyond the cut are lost too.
    - **Where:** `daemon/llm/memory.py:28-34`, `daemon/memory_worker.py:89-109`,
      `daemon/router.py:2466-2500`.
    - **Fix:** cap only what goes into the prompt; never write a truncated load
      back to disk.
90. **A failed send throws the draft away** (review #19) [checked]
    - A bad address (`Chris <c@x.com>`, `a@x.com; b@y.com`, a typo) or Gmail
      being unreachable: the daemon answers `{ok: false}`, v4 only toasts it, and
      the compose window is already closed. The existing test covers only the
      connection-drop path.
    - **Where:** `ui_v4/overlays.py:426-449`.
    - **Fix:** reopen the draft when `ok` is false, and accept "Name <addr>" plus
      `,` / `;` lists.
91. **Canvas undoes your own changes to Canvas todos on every sync** (review #20) [probe ×2]
    - Move a homework's due date earlier and the next sync puts it back. Un-tick
      one (resubmission needed) and it gets re-ticked. "Sync now" always reports
      "N todos ticked off" because it re-applies every time.
    - **Where:** `daemon/connectors/canvas_reconcile.py:46-54`.
    - **Fix:** only push when Canvas's own value changed (store the last-applied
      value).
92. **Canvas creates duplicate Google Calendar events** (review #21) [checked; probe]
    - Any failed update (Google 503, expired token) is treated as "you deleted the
      event", so the next sync creates a second one. The orphan keeps the old date
      forever.
    - Pressing "Keep" in the Review queue unlinks the event, so the next sync
      duplicates it.
    - Stale queued removals are never dropped when an item comes back.
    - **Where:** `daemon/connectors/canvas_calendar.py:77-90`,
      `daemon/connectors/gcal.py:322-338`, `daemon/connectors/canvas_queue.py:63`.
    - **Fix:** demote only on 404/410, and mark "kept" instead of unlinking.

### Features that don't work at all

93. **"Ask Lumen to revise" in compose always says "Revision failed."** (review #22) [checked]
    - The daemon returns `{subject, body}` with no `ok`, and v4 checks `ok`.
    - **Where:** `daemon/router.py:2078-2087` vs `ui_v4/overlays.py:466`. One-line
      fix.
94. **A compose window opened from chat holds the daemon's exclusive lock for up to 30 minutes** (review #23) [checked ×2]
    - While it's open these all queue silently: Revise, another question, the
      briefing, archive/delete, calendar writes. Then they fire late, e.g. a
      stale "Archive email?" confirm pops up after the draft closes. The same
      happens for 120 s whenever a chat is waiting on any confirm.
    - **Where:** `daemon/ipc_server.py:79-81`, `daemon/router.py:757-782,3212`.
    - **Fix:** hold the model lock only around generation, and use a separate
      one-dialog-at-a-time lock.
95. **The rule checkbox "Also label the N matching emails" never appears** (review #24) [checked ×3]
    - The daemon sends `check: {label, checked}` and v3/v4 read `check_label`.
      The backfill never runs. This is the "emails didn't get the new label" half
      of archived Josh's Finds #36.
    - **Where:** `ui_v4/overlays.py:237-240` (and `ui_v3/overlays.py:136`) vs
      `daemon/router.py:3379-3392`.
96. **When the tool loop hits its step limit, the recovered answer is thrown away** (review #25) [checked]
    - The salvage answer from archived Josh's Finds #45 arrives flagged `capped`,
      and the router replaces it with "(stopped after several tool steps…)". In
      Local mode that wastes a full generation.
    - **Where:** `daemon/router.py:3561-3562`;
      `test_chat_capped_yields_stopped_message` pins the old behaviour.
97. **Turning the model Off doesn't stop work already queued or running** (review #26) [checked ×3]
    - A distill scheduled 2 minutes after the last chat still runs. Canvas
      enrichment, a running suggest-labels batch and an in-progress answer keep
      calling Ollama after the unload. That reloads the 4B for 10 minutes right
      after you said Off.
    - **Where:** `daemon/llm/backend.py:180-182` (Off still routes to Ollama),
      `daemon/memory_worker.py:65-69` (no re-check after the delay),
      `daemon/router.py:1135-1148`.
    - **Fix:** `LLMBackend` refuses calls when Off, and the worker re-checks after
      its sleep.
98. **The Todos quick-add hint teaches a format the parser doesn't accept** (review #27) [checked]
    - The hint says "a weekday like 'friday' gives it a due date" and the
      placeholder is "Renew the domain friday #admin", but the parser only
      understands `@friday`. The date stays in the text, there's no due date, and
      the todo never shows in "Due today". Same for "remind me to pay rent
      friday" in chat.
    - **Where:** `ui_v4/screens/todos.py:533-542`,
      `daemon/connectors/todo_parse.py:246-263`.
    - **Fix:** resolve plain date phrases on capture (or at least fix the hint).
99. **The New-event form ignores the daemon's reply and loses your input on error** (review #28) [probe]
    - No toast for success or for "Couldn't reach Google Calendar". Today never
      shows the new event. The form closes before validation, so "9:30", "2pm" or
      a blank End is rejected after everything typed is gone. The edit dialog
      accepts 9:30; create doesn't, and has no all-day option.
    - **Where:** `ui_v4/overlays.py:542-559`, `ui_v4/screens/today.py:447-449`,
      `ui_v4/screens/calendar.py:765-773`.

---

## Review 2026-10-11 — Tier 2: real bugs, less frequent or lower impact

### Mail

100. **"Reply to this" from the Ask bar ignores the open email** (review #29) [probe ×3]
     - To is left empty or picks the newest loose match (a test reply went to
       news@costco.com). The draft never sees the email it replies to. A chat
       reply can also be addressed to *you*, when the newest match is your own
       sent reply.
     - **Where:** `daemon/router.py:2340-2345,3131-3183`.
101. **Plain-text emails are rendered as rich text** (review #30) [probe]
     - "a < b and c > d" loses its middle, `<b>Note</b>` collapses lines, and URLs
       aren't clickable. A regression of archived first-list #16/#17 in v3/v4.
       Canvas announcements have the same problem.
     - **Where:** `ui_v4/components.py:1187`, `ui_v4/screens/canvas.py:193-197`.
102. **HTML mail with dark header/footer/button bands becomes unreadable** (review #31) [probe, rendered]
     - Pale text colours are dropped but dark CSS backgrounds are kept: dark text
       on dark.
     - **Where:** `ui_v2/mail_html.py:167,346-353,373`.
103. **Unread counts follow whatever list Mail loaded last** (review #32) [probe ×2]
     - View Sent or run a search and the sidebar badge and Today say "Inbox clear"
       with unread mail waiting. The count is also capped at 50.
     - **Where:** `ui_v2/state.py:769-773`.
104. **Gmail-side trash, spam and drafts aren't handled** (review #33) [probe with a fake Gmail]
     - Mail trashed in Gmail stays in Lumen. Spam and half-written drafts get
       mirrored, and can come back as "your most recent email". Mail restored from
       Trash after a Lumen delete never reappears.
     - **Where:** `daemon/connectors/email_menu.py:276-326,356-359,610-636`.
105. **Cc is silently dropped** (review #34) [probe]
     - Compose has no Cc field and always sends `cc: []`, even when the drafted
       email had one.
     - **Where:** `ui_v4/overlays.py:360-389`.
106. **Esc or X in compose discards the draft** with no prompt or undo (review #35) [probe]
     - **Where:** `ui_v4/overlays.py:94-96,391-398`.
107. **The suggested-labels review closes on any outside click or Esc and discards every suggestion** (review #36) [probe]
     - That throws away up to a 40-message model run.
     - **Where:** `ui_v4/overlays.py:690-691,740-745`.
108. **Clicking a label that only exists in a rule shows an "unknown label" toast** (review #37) [probe]
     - Plus the previous mailbox's mail under the new label's title.
     - **Where:** `daemon/router.py:1906-1911`, `ui_v2/state.py:854-857`.
109. **Star / label / mark-unread reload the list** (review #38) [probe]
     - The open email's scroll position resets and it flashes "Loading message…".
     - If you were searching, the list jumps back to Inbox while the search box
       still shows your query.
     - After archive or delete, selection jumps to the top email instead of the
       next one.
     - **Where:** `ui_v2/state.py:814-827,865-880,933-936`.
110. **Opening mail while offline marks it read in Lumen but never in Gmail** (review #39) [probe]
     - The comment claiming the next poll fixes it is wrong.
     - **Where:** `daemon/router.py:1968-1981`.
111. **Sending after the 30-minute compose timeout gives no feedback** (review #40) [traced]
     - It does send, but there's no toast, and a failure loses the draft.
     - **Where:** `daemon/router.py:1674-1692`, `ui_v2/state.py:259-267`.
112. **Remote images load one at a time with no overall deadline** (review #41) [probe for timing]
     - 20 images at 250 ms each kept "Loading message…" up for 5 s. The cache of
       inlined HTML grows for the life of the process, and the loader will fetch
       localhost/LAN URLs.
     - **Where:** `ui_v2/mail_html.py:83-99`.

### Calendar, todos, Today

113. **Multi-day and past-midnight events only show on their first day** (review #42) [probe]
     - A three-day trip appears on day 1 only.
     - **Where:** `ui_v2/state.py:115-118`, `daemon/connectors/gcal.py:52-57`.
114. **Today never refreshes todos** (review #43) [traced]
     - After restoring from the tray in the morning, items that became due today
       sit in "upcoming", and Canvas-created todos never appear until another
       screen refreshes.
     - **Where:** `ui_v4/screens/today.py:139-153`, `ui_v2/state.py:40-52`.
115. **Today doesn't update while you look at it** (review #44) [traced]
     - "Standup in 12 min" never counts down, and at midnight it keeps showing
       yesterday. Calendar has a midnight rollover; Today doesn't. The Calendar
       view also reopens on yesterday the next day.
     - **Where:** `ui_v4/screens/today.py:233-240`,
       `ui_v4/screens/calendar.py:592,652-671`.
116. **Outside the 30-days-back / 60-days-ahead sync window, Calendar shows "Nothing scheduled"** (review #45) [traced]
     - The Ask bar then tells the model you're free.
     - **Where:** `ui_v4/screens/calendar.py:675-715`,
       `daemon/connectors/gcal.py:115-118`. See also #191.
117. **↻ on Calendar looks successful even when Google sync failed** (review #46) [traced]
     - It keeps showing stale events, and there's no "last synced" time.
     - **Where:** `daemon/router.py:1789-1790`.
118. **Some relative dates resolve wrong, and override a correct model answer** (review #47) [probe]
     - "a week from today" → today; "the friday after next" → this Friday;
       "dinner tomorrow before the recital on friday" → Friday.
     - **Where:** `daemon/connectors/todo_parse.py:197-215`,
       `daemon/llm/event_create.py:246-247`.
119. **Event proposals in Google's own format crash the turn** (review #48) [probe]
     - A recurrence list or a location object gives "internal error".
     - **Where:** `daemon/llm/event_create.py:111,121`; `daemon/router.py:2862`
       catches only `LLMUnavailable`.
120. **The update-event confirm is hard to read** (review #49) [traced]
     - Raw ISO times; colour/location-only changes don't name the event; it
       doesn't say attendees aren't notified.
     - **Where:** `daemon/router.py:2975-2995`.
121. **Free-slot parsing gets some phrasings wrong** (review #50) [probe]
     - "1.5 hours" → 300 min; "an hour and a half" → 60; "this weekend" said on a
       Sunday gives next weekend.
     - **Where:** `daemon/connectors/free_slots.py:28-60`.
122. **Accepting a commitment suggestion re-parses its text** (review #51) [probe]
     - "Send invoice #4471 to Chris" becomes a todo tagged "4471".
     - **Where:** `daemon/router.py:1873-1874`.
123. **Quick-add lowercases tags** ("#AbellCRM" → "abellcrm") (review #52) [probe]
     - Only the edit path got archived Josh's Finds #48's fix.
     - **Where:** `daemon/connectors/todo_parse.py:258`.
124. **Meeting prep ignores the day you name** (review #53) [probe]
     - "prep me for tomorrow's standup" returns today's.
     - **Where:** `daemon/llm/meeting_prep.py:116-120,158-184`.
125. **Declined invites and "free" events show as busy** and block free slots (review #54) [traced]
     - **Where:** `daemon/connectors/gcal.py:191-211`.

### Canvas

126. **One course returning 401 is treated as "session expired"** (review #55) [traced; Canvas behaviour assumed]
     - That deletes the session and starts a sign-in loop if the course keeps
       401ing; Canvas also uses 401 for per-course permission errors. Any single
       course error aborts the whole sync.
     - **Where:** `daemon/connectors/canvas_client.py:55,65`,
       `daemon/connectors/canvas_sync.py:382-399`.
127. **Deleted or unpublished assignments stay in the Canvas tab, and their todos stay open forever** (review #56) [probe]
     - **Where:** `daemon/connectors/canvas_store.py:166-174`.
128. **Browsing Canvas in the in-app browser starts a full sync on almost every page** (review #57) [probe; token rotation assumed]
     - Every cookie change (e.g. a rotating `_csrf_token`) is handed to the daemon.
     - **Where:** `ui_v4/screens/canvas.py:1228-1243`,
       `daemon/connectors/canvas_sync.py:68-85`.
129. **After first connect or re-login the list doesn't update** until you leave and come back (review #58) [traced]
     - The follow-up sync gets "already syncing" and gives up.
     - **Where:** `ui_v4/screens/canvas.py:640-660`.
130. **Calendar alerts are never shown or cleared** (review #59) [probe]
     - "N calendar changes since you last looked" only grows, and the conflict
       scan flags an event as overlapping itself or another 11:59 PM deadline.
     - **Where:** `daemon/connectors/canvas_alerts.py`,
       `daemon/connectors/canvas_events.py:312`.
131. **Canvas chat tools are never given to the model** (review #60) [traced]
     - The Canvas MCP server is spawned and sits idle. "Any new Canvas
       announcements?" in chat has no data path.
     - **Where:** `daemon/router.py:467,3448`.
132. **A daemon error during Sync now leaves "Syncing…" up forever** (review #61) [probe]
     - **Where:** `ui_v2/state.py:406-414`.
133. **The announcement flagger sends raw HTML cut at 2,500 characters** (review #62) [probe]
     - A due date near the end never reaches the model, and the "not actionable"
       verdict is permanent. Neither prompt includes today's date, so a year-less
       "Thursday Oct 16" can come out as the wrong year.
     - **Where:** `daemon/llm/canvas_flag.py:281-289,321-323`,
       `daemon/llm/canvas_enrich.py:55-62,180-181`.
134. **Turning on Lumen-written details never mines exam dates from existing announcements** (review #63) [probe]
     - **Where:** `daemon/llm/canvas_enrich.py` (`find_exam_dates`),
       `daemon/router.py:1534-1544`.
135. **Sync now says "Up to date" even when every calendar write failed** (review #64) [traced]
     - The manual "Add to calendar" strip also counts and writes past-due items.
     - **Where:** `ui_v4/screens/canvas.py:200-217`,
       `daemon/connectors/canvas_store.py` (`pending_markers`).
136. **Saved-login keyring calls still run on the UI thread** (review #65) [traced]
     - On save, change, forget, and on login pages. A locked keyring freezes the
       window.
     - **Where:** `ui_v4/screens/canvas.py:1341,1420`.
137. **On first connect, every old announcement in the term gets classified** (review #66) [traced]
     - 8 per 20 minutes. That cold-loads the model for hours, or uses 100+ Claude
       calls.
     - **Where:** `daemon/connectors/canvas_store.py:292-295`.
138. **`canvas.push_due_dates` blocks the whole daemon while it writes to Google** (review #67) [traced]
     - **Where:** `daemon/router.py:3444-3457`.

### LLM layer and daemon

139. **A broken Claude CLI looks like an empty answer** ("Asking again usually works") (review #68) [probe]
     - Happens when the CLI exits non-zero with only stderr output, e.g. an option
       renamed by an auto-update.
     - **Where:** `daemon/llm/claude_cli.py:275-287,389-433`.
140. **An Ollama error mid-answer is swallowed**, and the half answer is shown as complete (review #69) [probe ×2]
     - A 500 (the known Vulkan runner crash) or context overflow is reported as
       "is the service running?".
     - **Where:** `daemon/llm/client.py:84-121`.
141. **A crash inside the tool loop is silent** (review #70) [probe]
     - No log line, and "(no answer)" in the UI, which breaks the guarantee from
       archived Josh's Finds #45.
     - **Where:** `daemon/router.py:3539-3574`.
142. **Memory bullets you keep reinforcing still expire 60 days after first written** (review #71) [probe]
     - **Where:** `daemon/llm/distill.py:70-88`.
143. **Background model work isn't queued behind your chats in Local mode** (review #72) [traced]
     - Two generations can run on the iGPU at once.
     - **Where:** `daemon/memory_worker.py`, `daemon/connectors/canvas_sync.py:182-200`,
       `daemon/connectors/procedures.py` (`_draft`).
144. **The Claude rate-limit message names the wrong reset time** (review #73) [probe]
     - It names the 5-hour window when it's the 7-day one that's exhausted.
       Background jobs can stay paused after the limit lifts.
     - **Where:** `daemon/llm/claude_cli.py:355-363`, `daemon/llm/backend.py:130-144`.
145. **Dismissed routine proposals come back every day**, costing a model call each time (review #74) [probe]
     - **Where:** `daemon/connectors/procedures.py:103-156`.
146. **A confirm that timed out (120 s) stays on screen** (review #75) [probe ×2]
     - Approving it then does nothing, silently.
     - **Where:** `daemon/confirm.py:13`, `ui_v4/overlays.py:196-275`.
147. **The todo list goes into the prompt with no size cap** (review #76) [traced]
     - Including every Canvas assignment. That costs iGPU prompt time on every
       todo-shaped turn.
     - **Where:** `daemon/router.py:714-744`.
148. **Small leaks** (review #77) [probe / traced]
     - `claude auth status` keeps running after its timeout
       (`daemon/llm/claude_cli.py:572-583`).
     - Prompt temp files with your context survive a crash
       (`daemon/llm/claude_cli.py:401-432`).
     - An abandoned Google sign-in waits forever on a localhost port
       (`daemon/connectors/google_auth.py:67-84`).

### UI shell and screens

149. **With the daemon down or erroring, loading placeholders never settle** (review #78) [checked ×3]
     - Affects Settings (including the Stop/Quit buttons, the one time you need
       them), the Ask list, Books and Today. No `on_error` on those requests.
     - **Where:** `ui_v2/state.py:462-467,622-634,1215-1218`.
150. **Settings → Stop / Quit freezes the window for 5 s in `lumen` mode, and nothing restarts the stopped service** (review #79) [probe ×2]
     - Every action after that toasts "start it with: uv run lumen-daemon (or
       systemctl --user start lumen-daemon)". That unit doesn't exist, and
       there's no Start button.
     - **Where:** `ui_v4/screens/settings.py:852-865`, `instance_lock.py:104-130`.
151. **Super+L may do nothing when Lumen is open but not focused** (review #80) [needs you; traced]
     - On another workspace, or next to a focused browser. Hyprland has
       `misc:focus_on_activate = 0`, so `activateWindow()` only sets urgency. It
       works from the tray-hidden state, which may be what was tested.
     - **Josh:** please check whether Super+L brings Lumen forward when it's open
       behind another window.
     - **Where:** `ui_v4/app.py:95-110`.
     - **Fix:** hide + show to re-map, or a Hyprland `focusonactivate` window
       rule.
152. **"Sleep model now" from the tray mid-answer cuts the answer off** (review #81) [probe]
     - The next question shows the old answer's tail.
     - **Where:** `ui_v2/state.py:269-271`, `ui_v4/askbar.py:157`.
153. **Alt+← / Alt+→ still switch screens behind an open dialog** (review #82) [probe]
     - **Where:** `ui_v4/main.py:266-267`.
154. **"Continue in Ask Lumen" clicked while the Ask screen is streaming** sends the next follow-up to the wrong conversation (review #83) [probe]
     - **Where:** `ui_v4/askbar.py:367-380`.
155. **Only one toast at a time** (review #84) [traced]
     - Any toast replaces a pending Undo toast, so the Undo is lost. Raw daemon
       errors from background prefetches also show up as toasts.
     - **Where:** `ui_v4/overlays.py:1050-1069`.
156. **Super+L / Ask bar no longer pre-warm the model** (review #85) [traced]
     - That was v3's mitigation for archived first-list #7, and the "all v3 fixes
       are in v4" audit missed it. Each Ask-bar question after idle pays the full
       cold load.
     - **Where:** `ui_v4/app.py:100-102`.
     - **Fix:** warm on the first keystroke, not on summon.
157. **Files: "Ask about this file" uses the saved copy, not unsaved edits** (review #86) [checked]
     - v4 sends the buffer and the daemon ignores it.
     - **Where:** `daemon/router.py:2210-2223`.
158. **Files: Save rewrites characters you didn't touch** (review #87) [probe]
     - CRLF → LF, non-breaking space → space.
     - **Where:** `ui_v4/screens/files.py:632,657`.
159. **Files: in Claude mode the screen still says "Nothing here is uploaded" / "Files stay on this computer"** (review #88) [traced]
     - "Ask about this file" sends up to 6,000 characters and "Suggest an edit"
       the whole file to Anthropic. Settings' Claude description doesn't mention
       files, todos or memory either. How much to say is #192.
     - **Where:** `ui_v4/screens/files.py:395-396,517-518`,
       `ui_v4/screens/settings.py:303-306`.
160. **Books: invented titles pass the "grounded in a lookup" check** (review #89) [checked; probe]
     - Open Library's "No results for '<title>'" contains the title. Authors are
       never checked, and substring matching lets "The Way" pass for "The Way of
       Kings".
     - **Where:** `daemon/llm/book_recs.py:31-45`.
161. **An error mid-answer replaces the partial answer** with developer jargon (review #90) [probe]
     - **Where:** `ui_v4/askbar.py:171-177`.
162. **The Ask screen keeps saying "the model is turned off" after you turn it on** (review #91) [probe]
     - **Where:** `ui_v4/screens/ask.py:231,246-249`.
163. **"Open memory file" uses a hardcoded path** (review #92) [traced]
     - Ignores a configured `memory_path`, and fails before the first
       distillation.
     - **Where:** `ui_v4/screens/settings.py:736-746`.
164. **The model-switch toast confirms before the daemon does** (review #93) [traced]
     - A failed switch leaves the control showing the wrong mode.
     - **Where:** `ui_v4/screens/settings.py:431-437`.
165. **Files re-reads and rebuilds the whole folder on every visit, on the UI thread** (review #94) [probe]
     - ~230 ms for 3,000 entries.
     - **Where:** `ui_v4/screens/files.py:224-232`.

---

## Review 2026-10-11 — router misfires (Tier 3)

The same class as archived first-list #1 / #3: a keyword regex wins over what was
actually asked. All were checked by running real messages through a stubbed Router
[probe]. Fix as a cluster, with a regression test per phrasing; where there's no
tool for the ask, add the tool rather than just another guard regex.

166. **"brief" triggers the full daily briefing**
     - "give me a **brief** summary of…", "send a **brief** email to Sam".
     - **Where:** `BRIEFING_HINT`, `daemon/router.py:106`.
167. **"did Sam reply…" opens a compose window**
     - "did Sam **reply** to my email about the lease?"
     - **Where:** `COMPOSE_HINT`, `daemon/router.py:282-288`.
168. **"promise" / "owe" runs the commitment scan** (up to 20 model calls) and drops the real request
     - "explain javascript **promises**", "how much do I **owe**…", "email Sam
       saying I **promise**…".
     - **Where:** `PROMISE_HINT`, `daemon/router.py:157`.
169. **Unrelated asks go to event extraction, possibly a "Create event" confirm**
     - "recommend a book about coffee", "what's on my schedule for the meeting
       with the dean".
     - **Where:** `EVENT_HINT` (`.*`), `daemon/router.py:160-164`.
170. **"make a list of the files in Downloads" invents a listing and offers to write it to disk**
     - **Where:** `FILE_WRITE_HINT`, `daemon/router.py:211-216`.
171. **"write an email to my landlord about the documents" goes to the file writer instead of compose**
     - **Where:** `FILE_WRITE_HINT`, `daemon/router.py:211-216`.
172. **"tag my dentist todo as health" runs the mail-only tool loop** (could label an email instead)
     - **Where:** `MAIL_LABEL_HINT`, `daemon/router.py:189-193`.
173. **"help me prepare for my calculus exam" → "I don't see that meeting on your calendar"**
     - **Where:** `PREP_HINT`, `daemon/router.py:150`.
174. **"find a file I edited an hour ago" returns free calendar slots**
     - **Where:** `daemon/router.py:230-235`.
175. **Anything asked on the Files screen gets the file tool loop only**
     - "email Sam a summary of this file", "schedule…", "give me my briefing" get
       no compose / event / briefing.
     - **Where:** `daemon/router.py:2294-2306`.
176. **"book the first one" (which the Ask bar itself suggests after free slots) can't work**
     - Ask-bar turns have no history.
     - **Where:** `daemon/router.py:2376-2380`.
177. **"add a todo to call mom" / "add 5 and 7"**
     - Todo text becomes "to call mom"; "add 5 and 7" becomes a todo called "5 and
       7".
     - **Where:** `TODO_ADD` / `ADD_LOOSE`, `daemon/router.py:110`.
178. **"no worries…", "no problem…" are logged as corrections** and weighted up in memory
     - **Where:** `CORRECTION_HINT`, `daemon/router.py:387-390`.
179. **The identity prompt promises a compose window on routes that can't open one**
     - It tells the model "a compose window opens with your draft" everywhere.
     - **Where:** `router.IDENTITY`, `daemon/router.py`.

---

## Needs Josh's decision — friction and product calls

These change how something works or looks, so they wait for Josh's answer. Each
has a suggested default.

180. **Canvas tab ordering** — ❓ Josh to decide
     - All past work, including graded and submitted items, sits in an "Overdue"
       section at the top, oldest first. With 30 graded homeworks, Friday's
       project is row 35.
     - *Suggest:* a collapsed "Done" section, Overdue only for unsubmitted work,
       upcoming first.
181. **Canvas when the session expires in the background** — ❓ Josh to decide
     - The tab replaces the assignments/announcements with the first-run "Bring
       your coursework into Lumen" card, and nothing elsewhere says you were
       signed out.
     - *Suggest:* keep the lists, plus a "Signed out of Canvas — Sign in" strip.
182. **Canvas at term end** — ❓ Josh to decide
     - Every event from ended courses is queued for removal, labelled "Its course
       is turned off", and can only be kept one at a time.
     - *Suggest:* don't remove past events at all, or add "Keep all".
183. **Canvas announcements in the briefing** — ❓ Josh to decide
     - It shows the 5 newest every day regardless of age, including ended courses.
     - *Suggest:* only unseen ones from the last few days. Also add a "due
       tomorrow" line and cap the overdue list at "5 + N more".
184. **A Stop button for answers in progress** — ❓ Josh to decide
     - A slow answer currently locks both Ask surfaces and everything queued
       behind it.
185. **Model Off blocks things that don't need the model** — ❓ Josh to decide
     - "add a todo: X", "mark X done" and the free-slot finder are refused while
       Off.
     - *Suggest:* let those through.
186. **Today screen: clicking a todo** — ❓ Josh to decide
     - It jumps to the Todos screen and you have to find the item. Should it open
       the todo's detail directly?
187. **Todo order** — ❓ Josh to decide
     - Lists are in creation order; todo-system.md says by due date.
     - *Suggest:* sort by due date within each group, overdue first. Separate from
       the deferred #71 redesign.
188. **Mail completeness** — ❓ Josh to decide which of these to build
     - a Cc field (see #105)
     - Reply-all
     - honour Reply-To (no-reply senders)
     - "Show more" past 50 per mailbox
     - Mail re-reads new mail while you sit on it (email-menu.md promises a
       5-minute re-read; v4 has none)
     - inline `cid:` images
     - an "Open in Gmail" link for attachments
189. **Undo for deletes, and Books editing** — ❓ Josh to decide
     - Conversations and mail rules delete instantly with no Undo, unlike todos.
     - Books have no delete or edit at all. Enter in Title adds a book with no
       author and a default 5 stars, and a suggestion's "Add to log" logs it as a
       finished 5-star read.
190. **Files opens by extension, not content** — ❓ Josh to decide
     - README, Makefile, .bashrc, .gitignore, .csv, .c/.h and .gd/.tscn are
       greyed out as "not text".
     - *Suggest:* decide by content (the reader already detects binary and
       non-UTF-8 files).
191. **Calendar affordances** — ❓ Josh to decide
     - Should the edit dialog show/clear location and edit the description?
     - Should there be keys for prev / next / today / new?
     - Should events beyond the sync window be fetched on demand, or just labelled
       "Lumen keeps 30 days back to 60 ahead"? (See #116.)
192. **Claude-mode wording** — ❓ Josh to decide
     - Should Files and Settings say plainly what is sent to Claude when Claude
       mode is on? #159 is the factual error; this is about how much to say.
193. **Follow-ups after "Continue in Ask Lumen" lose the page context** — ❓ Josh to decide
     - The file or the open email is gone, so "expand step 2" or "label it" can't
       resolve.
     - *Suggest:* carry the context with the seeded conversation.
194. **Ask history paging** — ❓ Josh to decide
     - Only the newest 50 conversations are reachable. Add paging?

---

## Josh's Finds

New finds go here. Next free number: **195**.
