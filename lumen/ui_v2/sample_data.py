"""Sample content lifted verbatim from the mockup's state.

Screens render from this module through their populate() methods so real
daemon data can replace it without touching layout code.
"""

TODAY_ISO = "2026-07-06"

TODOS = [
    {"id": "t1", "text": "Call the dentist", "group": "today", "tag": "personal", "done": False},
    {"id": "t2", "text": "Reply to Priya re: sync defaults", "group": "today", "tag": "work", "done": False},
    {"id": "t3", "text": "Water the plants", "group": "today", "tag": "home", "done": True},
    {"id": "t4", "text": "Draft Lumen README", "group": "upcoming", "tag": "work", "due": "Sat", "done": False},
    {"id": "t5", "text": "Renew lumen.sh domain", "group": "upcoming", "tag": "admin", "due": "Jul 9", "done": False},
    {"id": "t6", "text": "Read “Systemantics”", "group": "none", "tag": "personal", "done": False},
    {"id": "t7", "text": "Try river WM on the laptop", "group": "none", "tag": "tinker", "done": False},
]

BOOKS = [
    {"id": "b1", "title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin", "done": "Jun 28",
     "rating": 5, "notes": "Slow burn, worth it. Gethen worldbuilding stuck with me."},
    {"id": "b2", "title": "Project Hail Mary", "author": "Andy Weir", "done": "Jun 10",
     "rating": 4, "notes": "Propulsive. Rocky carries it more than the science."},
    {"id": "b3", "title": "The Dispossessed", "author": "Ursula K. Le Guin", "done": "May 22",
     "rating": 5, "notes": "Ambiguous utopia. Reread candidate."},
    {"id": "b4", "title": "Piranesi", "author": "Susanna Clarke", "done": "Apr 30",
     "rating": 4, "notes": "Quiet, strange, lingers for weeks."},
]

MAILS = [
    {"id": "m1", "from": "GitHub", "subj": "PR #142: swap to local model runtime", "time": "08:12",
     "date": "Thu · 08:12", "unread": True,
     "preview": "All checks passed. 2 approvals — ready to merge.",
     "body": "nirmal approved your changes.\n\nAll checks have passed (build, lint, 142 tests). "
             "This PR swaps the remote inference call for the local Ollama runtime and adds the "
             "idle-unload timer.\n\nMerge whenever you’re ready."},
    {"id": "m2", "from": "Priya Nair", "subj": "Re: sync interval defaults", "time": "07:40",
     "date": "Thu · 07:40", "unread": True,
     "preview": "Agreed on 15 min — can we expose it in the config?",
     "body": "Thanks for writing this up.\n\n15 minutes as the default feels right — long enough to "
             "stay quiet, short enough that the tray never goes stale. Can we expose it under [sync] "
             "so power users can drop it to 5?\n\n— Priya"},
    {"id": "m3", "from": "Dr. Okafor’s office", "subj": "Appointment reminder — Jul 9", "time": "Tue",
     "date": "Tue · 14:02", "unread": True,
     "preview": "Reminder: dental cleaning, Wed Jul 9 at 10:00.",
     "body": "This is a reminder of your upcoming appointment:\n\nDental cleaning — Wednesday, July 9 "
             "at 10:00 AM.\n\nReply CONFIRM to keep this slot, or call the office to reschedule."},
    {"id": "m4", "from": "Sarah Chen", "subj": "Book club: next pick?", "time": "Mon",
     "date": "Mon · 19:20", "unread": True,
     "preview": "You’ve been on a Le Guin run — want to lead?",
     "body": "We’re choosing the next book-club read. You’ve clearly been on a Le Guin run "
             "lately — want to lead a session on The Dispossessed? Everyone’s keen.\n\nSarah"},
    {"id": "m5", "from": "Linux Weekly", "subj": "Wayland color management lands", "time": "Wed",
     "date": "Wed · 06:00", "unread": False,
     "preview": "The long-awaited color-management protocol merged.",
     "body": "This week in Linux: the color-management protocol finally merged into wlroots, HDR test "
             "builds land for Hyprland, and a roundup of tiling-WM dotfiles."},
    {"id": "m6", "from": "Calendar", "subj": "Invitation: Design review: Lumen", "time": "Wed",
     "date": "Wed · 09:15", "unread": False,
     "preview": "Today 15:30 – 16:15 · you accepted.",
     "body": "Design review: Lumen\nToday · 15:30 – 16:15\nGuests: Priya, you, +2\n\nYou accepted "
             "this invitation."},
    {"id": "m7", "from": "lumen digest", "subj": "Your week: 3 books, 12 todos closed", "time": "Mon",
     "date": "Mon · 07:00", "unread": False,
     "preview": "A quiet, productive week. Here’s the recap.",
     "body": "Your week in Lumen:\n\n• 3 books finished (2× Le Guin, 1× Weir)\n• 12 todos closed, "
             "7 still open\n• 4 events attended\n\nHave a good one."},
]

CAL_ITEMS = [
    {"date": "2026-07-05", "start": "08:00", "dur": 90, "title": "Long run", "cal": "health"},
    {"date": "2026-07-05", "start": "18:00", "dur": 120, "title": "Sunday roast @ mum’s", "cal": "social"},
    {"date": "2026-07-06", "start": "09:30", "dur": 30, "title": "Standup — Platform", "cal": "work"},
    {"date": "2026-07-06", "start": "11:00", "dur": 30, "title": "1:1 with Priya", "cal": "work"},
    {"date": "2026-07-06", "start": "13:00", "dur": 60, "title": "Lunch", "cal": "personal"},
    {"date": "2026-07-06", "start": "15:30", "dur": 45, "title": "Design review: Lumen", "cal": "work"},
    {"date": "2026-07-06", "start": "18:00", "dur": 60, "title": "Gym", "cal": "health"},
    {"date": "2026-07-07", "start": "09:30", "dur": 30, "title": "Standup — Platform", "cal": "work"},
    {"date": "2026-07-07", "start": "13:00", "dur": 90, "title": "Sprint planning", "cal": "work"},
    {"date": "2026-07-07", "start": "19:00", "dur": 90, "title": "Book club: The Dispossessed", "cal": "social"},
    {"date": "2026-07-08", "start": "09:30", "dur": 30, "title": "Standup — Platform", "cal": "work"},
    {"date": "2026-07-08", "start": "10:30", "dur": 120, "title": "Deep work — sync engine", "cal": "work"},
    {"date": "2026-07-08", "start": "12:30", "dur": 60, "title": "Lunch w/ Sam", "cal": "social"},
    {"date": "2026-07-09", "start": "09:30", "dur": 30, "title": "Standup — Platform", "cal": "work"},
    {"date": "2026-07-09", "start": "10:00", "dur": 60, "title": "Dental cleaning", "cal": "health"},
    {"date": "2026-07-09", "start": "16:00", "dur": 60, "title": "Release review", "cal": "work"},
    {"date": "2026-07-10", "start": "09:30", "dur": 30, "title": "Standup — Platform", "cal": "work"},
    {"date": "2026-07-10", "start": "14:00", "dur": 60, "title": "Sprint retro", "cal": "work"},
    {"date": "2026-07-10", "start": "18:00", "dur": 60, "title": "Gym", "cal": "health"},
    {"date": "2026-07-11", "start": "09:00", "dur": 240, "title": "Hiking — Ridge trail", "cal": "social"},
    {"date": "2026-07-14", "start": "13:00", "dur": 120, "title": "Quarterly planning", "cal": "work"},
    {"date": "2026-07-20", "start": "09:00", "dur": 480, "title": "PTO", "cal": "personal"},
    {"date": "2026-07-24", "start": "15:00", "dur": 60, "title": "Conf talk: local-first LLMs", "cal": "work"},
    {"date": "2026-07-29", "start": "18:30", "dur": 90, "title": "Ollama meetup", "cal": "social"},
]

# Today's schedule as used on the dashboard + launcher calendar response
EVENTS = [
    {"time": "09:30", "title": "Standup — Platform", "dur": "30m", "next": False},
    {"time": "11:00", "title": "1:1 with Priya", "dur": "30m", "next": False},
    {"time": "13:00", "title": "Lunch", "dur": "1h", "next": False},
    {"time": "15:30", "title": "Design review: Lumen", "dur": "45m", "next": True},
    {"time": "18:00", "title": "Gym", "dur": "1h", "next": False},
]

RECS = [
    {"title": "A Fire Upon the Deep", "author": "Vernor Vinge",
     "why": "Big-idea space opera near Project Hail Mary’s energy, but denser."},
    {"title": "The Word for World Is Forest", "author": "Ursula K. Le Guin",
     "why": "You rated two Le Guin novels 5★ — the short Hainish entry you haven’t logged."},
    {"title": "Solaris", "author": "Stanisław Lem",
     "why": "Shares Piranesi’s uncanny, unknowable-space mood."},
]

RECENTS = [
    {"glyph": "✓", "text": "Added todo “Call the dentist”", "meta": "2m"},
    {"glyph": "✉", "text": "Drafted reply to Priya", "meta": "12m"},
    {"glyph": "▲", "text": "Created event “Design review”", "meta": "1h"},
]

TRIES = [
    "what’s on my calendar today",
    "add todo: renew domain by friday",
    "recommend a book like the last two I finished",
    "summarize unread email from Priya",
]

CMDS = [
    {"c": "/cal", "d": "calendar & events"},
    {"c": "/todo", "d": "add or list todos"},
    {"c": "/mail", "d": "search the inbox"},
    {"c": "/book", "d": "log or recommend"},
    {"c": "/ask", "d": "general question"},
    {"c": "/set", "d": "open settings"},
]

CAL_SUMMARY = "5 events today · next: Design review at 15:30"

TEXT_ANSWER = (
    "A tiling window manager arranges windows in non-overlapping frames that fill the screen "
    "automatically, so you rarely drag or resize. Hyprland is a dynamic Wayland compositor in "
    "that family — it adds animations, rounded corners, and per-workspace layouts on top of "
    "the tiling model."
)
