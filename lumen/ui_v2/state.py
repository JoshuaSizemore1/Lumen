"""Mutable UI state + change signals.

Two modes, same surface for the screens:

* **sample mode** (`AppState()` with no clients) — seeded from `sample_data`,
  writes mutate in memory. Keeps offscreen screenshots and widget tests working
  without a daemon.
* **live mode** (`AppState(data=…, chat=…, confirm=…)`) — reads and writes go to
  the daemon over IPC; daemon rows are normalized here into the shapes the
  screens already expect, so the screens don't care which mode they're in.

This module is the only place that knows the daemon's row shapes; it is the seam
the mock was built around.
"""
import copy
import time
from datetime import date, datetime

from PyQt6.QtCore import QObject, pyqtSignal

from . import sample_data as S
from . import theme as T


def _due_label(d: date, today: date) -> str:
    if 0 < (d - today).days <= 6:
        return d.strftime("%a")
    return f"{d.strftime('%b')} {d.day}"


def _finished_label(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def _norm_todo(row: dict, today: date) -> dict:
    """Daemon todo {id,text,completed,due_date,tags,created_at} -> screen shape.

    Group + due chip are derived here the same way the old todo screen did it:
    due<=today (overdue folds in) -> TODAY with no chip, future -> UPCOMING with
    a chip, none -> NO DATE."""
    due_iso = row.get("due_date")
    if not due_iso:
        group, due = "none", None
    elif date.fromisoformat(due_iso) <= today:
        group, due = "today", None
    else:
        group, due = "upcoming", _due_label(date.fromisoformat(due_iso), today)
    tags = list(row.get("tags") or [])
    return {"id": row["id"], "text": row["text"], "done": bool(row["completed"]),
            "group": group, "due": due, "tags": tags,
            "tag": tags[0] if tags else ""}


def _norm_book(row: dict) -> dict:
    """Daemon book {id,title,author,rating,notes,date_finished} -> screen shape."""
    return {"id": row["id"], "title": row["title"], "author": row.get("author") or "",
            "rating": row.get("rating") or 0, "notes": row.get("notes") or "",
            "done": _finished_label(row.get("date_finished"))}


def _norm_mail(r: dict) -> dict:
    """Daemon email-mirror row -> screen shape ({from,subj,preview,time,date,unread,...})."""
    sender = r.get("sender", "")
    name = sender.split("<")[0].strip().strip('"') or sender
    received = r.get("received_at") or ""
    try:
        dt = datetime.fromisoformat(received).astimezone()
        time_s = dt.strftime("%H:%M") if dt.date() == datetime.now().date() \
            else dt.strftime("%b %d")
        date_s = dt.strftime("%a, %b %d")
    except ValueError:
        time_s = date_s = ""
    return {"id": r["id"], "from": name, "subj": r.get("subject") or "(no subject)",
            "preview": r.get("snippet", ""), "time": time_s, "date": date_s,
            "unread": not r.get("is_read", True), "body": r.get("body", ""),
            "labels": r.get("labels", []), "attachments": r.get("attachments", [])}


def _norm_rec(rec: dict) -> dict:
    """Daemon rec {title,author,rationale} -> screen shape ({...,why})."""
    return {"title": rec["title"], "author": rec.get("author") or "",
            "why": rec.get("rationale") or rec.get("why") or ""}


def _norm_event(e: dict, tz) -> dict:
    """Daemon calendar event -> uniform screen event carrying its own color hex.

    Time grids paint by `color` (real Google-calendar colors don't map to the
    mock's four categories), and `cal` (calendar name) is the day-view meta."""
    color = e.get("color") or T.TEXT_DIM
    cal = e.get("calendar_name") or ""
    if e.get("all_day"):
        return {"date": e["start_at"][:10], "start": "", "start_min": 0,
                "dur": 0, "title": e.get("title") or "Untitled", "cal": cal,
                "color": color, "all_day": True}
    s = datetime.fromisoformat(e["start_at"]).astimezone(tz)
    if e.get("end_at"):
        dur = max(int((datetime.fromisoformat(e["end_at"]).astimezone(tz)
                       - s).total_seconds() // 60), 15)
    else:
        dur = 30
    return {"date": s.date().isoformat(), "start": s.strftime("%H:%M"),
            "start_min": s.hour * 60 + s.minute, "dur": dur,
            "title": e.get("title") or "Untitled", "cal": cal, "color": color,
            "all_day": False}


def _sample_events() -> list[dict]:
    from .calendar_grids import _to_min
    return [{**e, "start_min": _to_min(e["start"]), "all_day": False,
             "color": T.CAL_COLORS.get(e["cal"], T.TEXT_DIM)} for e in S.CAL_ITEMS]


class AppState(QObject):
    todos_changed = pyqtSignal()
    mails_changed = pyqtSignal()
    books_changed = pyqtSignal()
    recs_changed = pyqtSignal()
    view_requested = pyqtSignal(str)      # tab key: launcher/dashboard/...
    open_chat_requested = pyqtSignal(int)  # hand a conversation off to the full Chat screen
    confirm_requested = pyqtSignal(dict)  # confirm-dialog payload (may carry confirm_id)
    toast_requested = pyqtSignal(str)
    accent_requested = pyqtSignal(str)    # accent hex from the settings picker
    status_requested = pyqtSignal(str)    # transient status line (daemon offline/errors)

    def __init__(self, data=None, chat=None, confirm=None):
        super().__init__()
        self._data = data        # one-shot requests: todos/books/calendar
        self._chat = chat        # launcher streaming client
        self._confirm = confirm  # dedicated confirm.response channel
        self.live = data is not None

        self.mail_connected = True
        self.mail_syncing = False
        self.mail_last_sync = None
        self.selected_mail = "m1"

        if self.live:
            self.todos, self.books, self.recs, self.mails = [], [], [], []
            self.mail_total = 0
            data.error.connect(self.status_requested)
            self.attach_confirm_source(data)
            if chat is not None:
                self.attach_confirm_source(chat)
            self.refresh_todos()
            self.refresh_books()
            self.refresh_mails()
        else:
            self.todos = [{**t, "tags": [t["tag"]] if t.get("tag") else []}
                          for t in copy.deepcopy(S.TODOS)]
            self.books = copy.deepcopy(S.BOOKS)
            self.recs = [_norm_rec(r) for r in S.RECS]
            self.mails = copy.deepcopy(S.MAILS)
            self.mail_total = len(self.mails)

    # ---- confirm-over-IPC routing ----
    def attach_confirm_source(self, client) -> None:
        """A daemon confirm_request on this client surfaces as the same in-window
        confirm overlay the mock uses; the answer travels back via `respond_confirm`."""
        client.confirm_requested.connect(self.confirm_requested.emit)

    def respond_confirm(self, confirm_id: int, approved: bool) -> None:
        if self._confirm is not None:
            self._confirm.respond_confirm(confirm_id, approved)

    def sleep_model(self) -> None:
        if self._chat is not None:
            self._chat.sleep_model()

    def warm_model(self) -> None:
        """Preload the model when the user engages the launcher, so the cold
        start happens behind their typing instead of after they hit enter."""
        if self._chat is not None:
            self._chat.send("warm", {})

    # ---- conversations (chat history) ----
    def list_conversations(self, cb) -> None:
        """cb(rows) with sidebar-shaped [{id, title, updated_at}], newest first."""
        if self._data is not None:
            self._data.request("conversations.list", {}, cb)
        else:
            cb([])   # sample mode has no transcript store

    def get_conversation(self, cid: int, cb) -> None:
        """cb({conversation, messages}) for reopening a past thread."""
        if self._data is not None:
            self._data.request("conversations.get", {"id": cid}, cb)

    # ---- todos ----
    def open_count(self) -> int:
        return sum(1 for t in self.todos if not t["done"])

    def _set_todos(self, rows: list[dict]) -> None:
        today = date.today()
        self.todos = [_norm_todo(r, today) for r in rows]
        self.todos_changed.emit()

    def refresh_todos(self) -> None:
        if self._data is not None:
            self._data.request("todos.list", {}, self._set_todos)

    def add_todo(self, text: str):
        text = text.strip()
        if not text:
            return
        if self._data is not None:
            self._data.request("todos.add", {"text": text}, self._set_todos)
        else:
            self.todos.insert(0, {"id": f"n{time.time()}", "text": text, "group": "today",
                                  "tag": "new", "tags": ["new"], "due": None, "done": False})
            self.todos_changed.emit()

    def toggle_todo(self, tid):
        if self._data is not None:
            cur = next((t for t in self.todos if t["id"] == tid), None)
            if cur is None:
                return
            self._data.request("todos.toggle", {"id": tid, "completed": not cur["done"]},
                               self._set_todos)
        else:
            for t in self.todos:
                if t["id"] == tid:
                    t["done"] = not t["done"]
            self.todos_changed.emit()

    def delete_todo(self, tid):
        if self._data is not None:
            self._data.request("todos.delete", {"id": tid}, self._set_todos)
        else:
            self.todos = [t for t in self.todos if t["id"] != tid]
            self.todos_changed.emit()

    # ---- mail (live from the daemon mirror; sample rows without a daemon) ----
    def unread_count(self) -> int:
        return sum(1 for m in self.mails if m["unread"])

    def unread_mails(self) -> list[dict]:
        return [m for m in self.mails if m["unread"]]

    def sel_mail(self) -> dict | None:
        return next((m for m in self.mails if m["id"] == self.selected_mail),
                    self.mails[0] if self.mails else None)

    def select_mail(self, mid: str):
        # Selecting only selects: read-state changes are explicit, confirmed
        # writes (decided 2026-07-12) — never a side effect of browsing.
        self.selected_mail = mid
        self.mails_changed.emit()

    def _set_mails(self, result: dict) -> None:
        # emails.search responses carry only {"emails": [...]} — no status
        # keys — so absent keys must fall back to the PRIOR state, not a
        # reset default, or a search while disconnected would flip the
        # status to "connected"/"synced".
        self.mails = [_norm_mail(r) for r in result.get("emails", [])]
        self.mail_connected = result.get("connected", self.mail_connected)
        self.mail_syncing = result.get("syncing", self.mail_syncing)
        self.mail_last_sync = result.get("last_sync", self.mail_last_sync)
        counts = result.get("counts")
        if counts:
            self.mail_total = counts.get("total", self.mail_total)
        if self.selected_mail not in {m["id"] for m in self.mails}:
            self.selected_mail = self.mails[0]["id"] if self.mails else None
        self.mails_changed.emit()

    def refresh_mails(self) -> None:
        if self._data is not None:
            self._data.request("emails.list", {}, self._set_mails)

    def refresh_inbox(self) -> None:
        """Manual refresh: delta-sync against Gmail, then reload the page."""
        if self._data is not None:
            self._data.request("mail.refresh", {}, self._set_mails)

    def search_mails(self, query: str) -> None:
        query = query.strip()
        if self._data is None:
            return
        if not query:
            self.refresh_mails()
            return
        self._data.request("emails.search", {"query": query}, self._set_mails)

    def _mail_action_done(self, result: dict) -> None:
        msg = result.get("message", "")
        self.toast_requested.emit(("✓ " if result.get("ok") else "") + msg)
        self.refresh_mails()

    def archive_mail(self, mid: str) -> None:
        if self._data is not None:
            self._data.request("emails.archive", {"id": mid}, self._mail_action_done)

    def set_mail_read(self, mid: str, read: bool) -> None:
        if self._data is not None:
            self._data.request("emails.mark_read", {"id": mid, "read": read},
                               self._mail_action_done)

    # ---- books ----
    def _set_books(self, rows: list[dict]) -> None:
        self.books = [_norm_book(r) for r in rows]
        self.books_changed.emit()

    def _set_recs(self, result: dict) -> None:
        self.recs = [_norm_rec(r) for r in result.get("recs", [])]
        self.recs_changed.emit()

    def refresh_books(self) -> None:
        if self._data is not None:
            self._data.request("books.list", {}, self._set_books)
            self._data.request("books.recs", {}, self._set_recs)

    def add_book(self, title: str, author: str, rating: int, notes: str):
        title = title.strip()
        if not title:
            return
        if self._data is not None:
            self._data.request("books.add", {"title": title, "author": author.strip(),
                                             "rating": rating, "notes": notes.strip()},
                               self._set_books)
        else:
            self.books.insert(0, {"id": f"nb{time.time()}", "title": title,
                                  "author": author.strip() or "Unknown", "done": "today",
                                  "rating": rating, "notes": notes.strip()})
            self.books_changed.emit()

    def recommend_books(self, on_done=None) -> None:
        """Generate fresh recommendations (LLM). No-op without a daemon."""
        if self._data is None:
            return

        def handle(result):
            self._set_recs(result)
            if on_done:
                on_done()
        self._data.request("books.recommend", {}, handle)

    # ---- calendar ----
    def fetch_calendar(self, frm: str, to: str, cb) -> None:
        """cb(result) with result = {events(normalized), connected, window, error}."""
        if self._data is not None:
            tz = datetime.now().astimezone().tzinfo

            def handle(result):
                cb({"events": [_norm_event(e, tz) for e in result.get("events", [])],
                    "connected": result.get("connected", True),
                    "window": result.get("window"), "error": None})
            self._data.request("calendar.list", {"from": frm, "to": to}, handle)
        else:
            evs = [e for e in _sample_events() if frm <= e["date"] <= to]
            cb({"events": evs, "connected": True, "window": None, "error": None})

    def create_event(self, proposal: dict, cb=None) -> None:
        """Daemon validates + gates behind the confirm overlay, then creates."""
        if self._data is not None:
            self._data.request("calendar.create", {"proposal": proposal},
                               cb or (lambda _r: None))
        else:
            # sample mode: show the mock confirmation so the button still demos
            self.confirm_requested.emit({
                "icon": "▲", "title": "Create calendar event",
                "intro": "Lumen will add this event to your Google Calendar.",
                "rows": [("Title", proposal.get("title", "")),
                         ("When", f"{proposal.get('start', '')} – {proposal.get('end', '')}"),
                         ("Calendar", "Personal (primary)")],
                "confirm_label": "Create event", "toast": "✓ Event added to calendar"})

    # ---- mock write confirmations (no daemon send path yet: Phase 7) ----
    def email_confirm(self):
        self.confirm_requested.emit({
            "icon": "✉", "title": "Send email",
            "intro": "Lumen will send this message from your connected Gmail account.",
            "rows": [
                ("To", "priya.nair@company.com"),
                ("Subject", "Re: sync interval defaults"),
                ("Body", "Agreed — let’s ship 15 min as the default and expose it in [sync]. "
                         "I’ll update the README today."),
            ],
            "confirm_label": "Send email", "toast": "✓ Email sent to Priya Nair",
        })

    def reply_confirm(self):
        m = self.sel_mail()
        subj = m["subj"] if m["subj"].lower().startswith("re:") else "Re: " + m["subj"]
        self.confirm_requested.emit({
            "icon": "✉", "title": "Send reply",
            "intro": "Lumen will send this reply from your connected Gmail account.",
            "rows": [
                ("To", m["from"]),
                ("Subject", subj),
                ("Body", "Thanks — sounds good. I’ll follow up shortly."),
            ],
            "confirm_label": "Send reply", "toast": "✓ Reply sent to " + m["from"],
        })
