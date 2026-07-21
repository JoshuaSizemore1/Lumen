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
import re
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
            "tag": tags[0] if tags else "",
            "description": row.get("description") or "",
            "due_date": due_iso, "source": row.get("source") or ""}


def _norm_book(row: dict) -> dict:
    """Daemon book {id,title,author,rating,notes,date_finished} -> screen shape."""
    return {"id": row["id"], "title": row["title"], "author": row.get("author") or "",
            "rating": row.get("rating") or 0, "notes": row.get("notes") or "",
            "done": _finished_label(row.get("date_finished"))}


def _norm_mail(r: dict) -> dict:
    """Daemon email-mirror row -> screen shape ({from,subj,preview,time,date,unread,...})."""
    sender = r.get("sender", "")
    name = sender.split("<")[0].strip().strip('"') or sender
    addr_m = re.search(r"<([^<>\s]+@[^<>\s]+)>", sender)
    addr = addr_m.group(1) if addr_m else (sender.strip() if "@" in sender else "")
    received = r.get("received_at") or ""
    try:
        dt = datetime.fromisoformat(received).astimezone()
        time_s = dt.strftime("%H:%M") if dt.date() == datetime.now().date() \
            else dt.strftime("%b %d")
        date_s = dt.strftime("%a, %b %d")
    except ValueError:
        time_s = date_s = ""
    return {"id": r["id"], "from": name, "from_addr": addr,
            "subj": r.get("subject") or "(no subject)",
            "preview": r.get("snippet", ""), "time": time_s, "date": date_s,
            "unread": not r.get("is_read", True), "body": r.get("body", ""),
            # list rows omit body_html (kept light); None = fetch on open
            "body_html": r.get("body_html"),
            "labels": r.get("labels", []), "label_names": r.get("label_names", []),
            "attachments": r.get("attachments", [])}


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
    # id/calendar_id ride along for the delete affordance; sample events have
    # neither, which is what hides delete in sample mode.
    ident = {"id": e.get("id"), "calendar_id": e.get("calendar_id")}
    if e.get("all_day"):
        return {**ident, "date": e["start_at"][:10], "start": "", "start_min": 0,
                "dur": 0, "title": e.get("title") or "Untitled", "cal": cal,
                "color": color, "all_day": True}
    s = datetime.fromisoformat(e["start_at"]).astimezone(tz)
    if e.get("end_at"):
        dur = max(int((datetime.fromisoformat(e["end_at"]).astimezone(tz)
                       - s).total_seconds() // 60), 15)
    else:
        dur = 30
    return {**ident, "date": s.date().isoformat(), "start": s.strftime("%H:%M"),
            "start_min": s.hour * 60 + s.minute, "dur": dur,
            "title": e.get("title") or "Untitled", "cal": cal, "color": color,
            "all_day": False}


def _sample_events() -> list[dict]:
    from .calendar_grids import _to_min
    return [{**e, "start_min": _to_min(e["start"]), "all_day": False,
             "color": T.CAL_COLORS.get(e["cal"], T.TEXT_DIM)} for e in S.CAL_ITEMS]


class AppState(QObject):
    todos_changed = pyqtSignal()
    suggestions_changed = pyqtSignal()
    procedures_changed = pyqtSignal()
    mails_changed = pyqtSignal()
    books_changed = pyqtSignal()
    recs_changed = pyqtSignal()
    view_requested = pyqtSignal(str)      # tab key: chat/dashboard/...
    open_chat_requested = pyqtSignal(int)  # hand a conversation off to the full Chat screen
    confirm_requested = pyqtSignal(dict)  # confirm-dialog payload (may carry confirm_id)
    compose_requested = pyqtSignal(dict)  # compose-popup payload (may carry compose_id)
    rule_edit_requested = pyqtSignal(dict)  # open the rule editor (prefill payload)
    toast_requested = pyqtSignal(str)
    accent_requested = pyqtSignal(str)    # accent hex from the settings picker
    status_requested = pyqtSignal(str)    # transient status line (daemon offline/errors)

    def __init__(self, data=None, chat=None, confirm=None):
        super().__init__()
        self._data = data        # one-shot requests: todos/books/calendar
        self._chat = chat        # launcher streaming client
        self._confirm = confirm  # dedicated confirm.response channel
        self.live = data is not None

        # THE active chat thread (todo-fixes #5/#6): every chat surface
        # (launcher palette, hotkey overlay, Chat screen) appends to this one
        # conversation. None = next prompt starts a fresh thread; only an
        # explicit "New chat" (or first-ever prompt) resets it.
        self.active_conv_id: int | None = None

        self.mail_connected = True
        self.mail_syncing = False
        self.mail_last_sync = None
        self._last_sync_req: float | None = None   # monotonic; sync debounce
        self.selected_mail = "m1"
        self.mail_scope = "inbox"      # "inbox" | "unread" | a label name
        self.mail_labels: list[str] = []
        self.mail_suggestions: dict[str, str] = {}
        # Remote images load on open (todo-fixes #18). Mirrors
        # [mail] load_remote_images; refreshed from the daemon on startup so a
        # user who turns it off gets the click-to-load bar back.
        self.load_remote_images = True

        self.suggestions: list[dict] = []
        self.proposed_procedures: list[dict] = []
        self.active_procedures: list[dict] = []
        self.manabi_due = False
        if self.live:
            self.todos, self.books, self.recs, self.mails = [], [], [], []
            self.mail_total = 0
            data.error.connect(self.status_requested)
            self.attach_confirm_source(data)
            self.attach_compose_source(data)
            if chat is not None:
                self.attach_confirm_source(chat)
                self.attach_compose_source(chat)
            self.refresh_todos()
            self.refresh_books()
            self.fetch_settings(self._apply_mail_settings)
            self.sync_inbox()    # launch → immediate Gmail delta-sync
            self.refresh_suggestions()
            self.refresh_procedures()
        else:
            self.todos = [{**t, "tags": [t["tag"]] if t.get("tag") else []}
                          for t in copy.deepcopy(S.TODOS)]
            self.books = copy.deepcopy(S.BOOKS)
            self.recs = [_norm_rec(r) for r in S.RECS]
            self.mails = copy.deepcopy(S.MAILS)
            self.mail_total = len(self.mails)
            self.mail_labels = sorted(
                {n for m in self.mails for n in m.get("label_names", [])})

    # ---- confirm-over-IPC routing ----
    def attach_confirm_source(self, client) -> None:
        """A daemon confirm_request on this client surfaces as the same in-window
        confirm overlay the mock uses; the answer travels back via `respond_confirm`."""
        client.confirm_requested.connect(self.confirm_requested.emit)

    def respond_confirm(self, confirm_id: int, approved: bool,
                        check: bool | None = None) -> None:
        if self._confirm is not None:
            self._confirm.respond_confirm(confirm_id, approved, check=check)

    # ---- compose popup (Phase 7) ----
    def attach_compose_source(self, client) -> None:
        """A daemon compose_request on this client opens the compose popup."""
        client.compose_requested.connect(self.compose_requested.emit)

    def open_compose(self, prefill: dict | None = None) -> None:
        """Mail-screen Compose/Reply: purely local popup — the daemon is only
        involved when the user hits Send."""
        self.compose_requested.emit(prefill or {})

    def send_email(self, fields: dict, cb) -> None:
        if self._data is not None:
            self._data.request("emails.send", fields, cb)
        else:
            cb({"ok": True, "message": "Sent (sample mode — nothing left the app)."})

    def revise_email(self, fields: dict, cb) -> None:
        if self._data is not None:
            self._data.request("emails.revise", fields, cb)

    def respond_compose(self, compose_id: int, fields: dict | None, cb=None) -> None:
        """Answer a chat-driven compose; fields=None cancels. Travels on the
        dedicated confirm client — the chat connection is blocked awaiting it."""
        if self._confirm is not None:
            self._confirm.request(
                "compose.response",
                {"compose_id": compose_id, "send": fields is not None,
                 "fields": fields or {}},
                cb or (lambda _r: None))

    def sleep_model(self) -> None:
        if self._chat is not None:
            self._chat.sleep_model()

    def warm_model(self) -> None:
        """Preload the model when the user engages the launcher, so the cold
        start happens behind their typing instead of after they hit enter."""
        if self._chat is not None:
            self._chat.send("warm", {})

    # ---- canvas (session handoff + status; password never crosses here) ----
    def canvas_set_session(self, cookies: dict, cb=None) -> None:
        if self._data is None:
            return
        self._data.request("canvas.set_session", {"cookies": cookies},
                           cb or (lambda _r: None))

    def canvas_status(self, cb) -> None:
        if self._data is None:
            cb({"connected": False, "last_sync": None, "enabled": False})
            return
        self._data.request("canvas.status", {}, cb)

    def canvas_disconnect(self, cb=None) -> None:
        if self._data is None:
            return
        self._data.request("canvas.disconnect", {}, cb or (lambda _r: None))

    def canvas_assignments(self, cb) -> None:
        if self._data is None:
            cb({"assignments": []}); return
        self._data.request("canvas.assignments", {}, cb)

    def canvas_announcements(self, cb) -> None:
        if self._data is None:
            cb({"announcements": []}); return
        self._data.request("canvas.announcements", {}, cb)

    def canvas_pending_calendar(self, cb) -> None:
        if self._data is None:
            cb({"markers": []}); return
        self._data.request("canvas.pending_calendar", {}, cb)

    def canvas_push_due_dates(self, cb) -> None:
        if self._data is None:
            return
        self._data.request("canvas.push_due_dates", {}, cb)

    def canvas_add_announcement_todo(self, ann_id: int, cb=None) -> None:
        if self._data is None:
            return
        self._data.request("canvas.add_announcement_todo", {"id": ann_id},
                           cb or (lambda _r: None))

    def canvas_courses(self, cb) -> None:
        if self._data is None:
            cb({"courses": []}); return
        self._data.request("canvas.courses", {}, cb)

    def canvas_set_course_included(self, course_id: int, included: bool,
                                   cb=None) -> None:
        if self._data is None:
            return
        self._data.request("canvas.set_course_included",
                           {"course_id": course_id, "included": included},
                           cb or (lambda _r: None))

    # ---- google (re-consent when the token expires) ----
    def google_reconnect(self, cb) -> None:
        """Kick off the browser consent flow in the daemon. cb(snapshot) fires
        with a fresh settings snapshot on success; a failure travels the normal
        error → status_requested channel, so cb only ever sees success."""
        if self._data is None:
            return
        self._data.request("google.reconnect", {}, cb)

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

    def seed_conversation(self, question: str, answer: str,
                          tools: list[str] | None, cb) -> None:
        """Turn an ephemeral Ask-Lumen exchange into a persisted chat thread
        (#24). cb(cid) fires with the new conversation id. No-op without a
        daemon (sample mode has no transcript store)."""
        if self._data is None:
            cb(None)
            return

        def handle(result):
            cb((result or {}).get("id"))
        self._data.request("conversations.seed",
                           {"question": question, "answer": answer,
                            "tools": tools or []}, handle)

    def delete_conversation(self, cid: int, cb=None) -> None:
        """Remove a thread (and its messages) from local storage. Local-only
        data, so no confirm ritual — same treatment as deleting a todo."""
        if self._data is not None:
            self._data.request("conversations.delete", {"id": cid},
                               cb or (lambda _r: None))

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

    _UNSET = object()

    def update_todo(self, tid, *, text=None, description=_UNSET,
                    due_date=_UNSET, tags=None):
        """Edit an existing todo from the detail card (#21/#23). Only the
        fields passed are sent; the daemon returns the fresh list."""
        payload = {"id": tid}
        if text is not None:
            payload["text"] = text
        if description is not self._UNSET:
            payload["description"] = description
        if due_date is not self._UNSET:
            payload["due_date"] = due_date
        if tags is not None:
            payload["tags"] = tags
        if self._data is not None:
            self._data.request("todos.update", payload, self._set_todos)
        else:
            for t in self.todos:
                if t["id"] == tid:
                    if text is not None:
                        t["text"] = text
                    if description is not self._UNSET:
                        t["description"] = description or ""
                    if tags is not None:
                        t["tags"] = list(tags)
                        t["tag"] = tags[0] if tags else ""
            self.todos_changed.emit()

    def delete_todo(self, tid):
        if self._data is not None:
            self._data.request("todos.delete", {"id": tid}, self._set_todos)
        else:
            self.todos = [t for t in self.todos if t["id"] != tid]
            self.todos_changed.emit()

    # ---- commitment suggestions (Phase 8 feature 3) ----
    def _set_suggestions(self, result: dict) -> None:
        self.suggestions = result.get("suggestions", [])
        if "todos" in result:              # accept returns the fresh todo list too
            self._set_todos(result["todos"])
        self.suggestions_changed.emit()

    def refresh_suggestions(self) -> None:
        if self._data is not None:
            self._data.request("todos.suggestions", {}, self._set_suggestions)

    def scan_commitments(self, cb=None) -> None:
        """User-triggered scan of sent mail; cb(result) gets {scanned, found}."""
        if self._data is None:
            if cb:
                cb({"scanned": 0, "found": 0, "suggestions": []})
            return

        def handle(result):
            self._set_suggestions(result)
            if cb:
                cb(result)
        self._data.request("todos.scan_commitments", {}, handle)

    def accept_suggestion(self, sid: int) -> None:
        if self._data is not None:
            self._data.request("todos.accept_suggestion", {"id": sid},
                               self._set_suggestions)

    def dismiss_suggestion(self, sid: int) -> None:
        if self._data is not None:
            self._data.request("todos.dismiss_suggestion", {"id": sid},
                               self._set_suggestions)

    # ---- memory: learned procedures (Phase 9) ----
    def _set_procedures(self, result: dict) -> None:
        self.proposed_procedures = result.get("proposed", [])
        self.active_procedures = result.get("active", [])
        self.procedures_changed.emit()

    def refresh_procedures(self) -> None:
        if self._data is not None:
            self._data.request("memory.procedures", {}, self._set_procedures)

    def fetch_settings(self, cb) -> None:
        """One-shot read of the live daemon config for the Settings screen."""
        if self._data is not None:
            self._data.request("settings.get", {}, cb)

    def _apply_mail_settings(self, snapshot) -> None:
        """Cache the image-loading preference so the reading pane can decide
        synchronously while painting. Defaults stay on if the read fails."""
        if isinstance(snapshot, dict):
            mail = snapshot.get("mail") or {}
            self.load_remote_images = bool(mail.get("load_remote_images", True))

    def approve_procedure(self, slug: str) -> None:
        if self._data is not None:
            self._data.request("memory.approve_procedure", {"slug": slug},
                               self._set_procedures)

    def dismiss_procedure(self, slug: str) -> None:
        if self._data is not None:
            self._data.request("memory.dismiss_procedure", {"slug": slug},
                               self._set_procedures)

    def remove_procedure(self, slug: str) -> None:
        if self._data is not None:
            self._data.request("memory.remove_procedure", {"slug": slug},
                               self._set_procedures)

    def fetch_learned(self, cb) -> None:
        """cb({text, updated_at, path}) — the distilled memory file verbatim,
        for the Settings 'what Lumen has learned' view."""
        if self._data is not None:
            self._data.request("memory.learned", {}, cb)
        else:
            cb({"text": "", "updated_at": None, "path": ""})

    def open_memory_file(self) -> None:
        """Open the hand-editable memory.md in the user's default editor."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        from lumen.daemon.config import default_memory_path
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(default_memory_path())))

    # ---- mail (live from the daemon mirror; sample rows without a daemon) ----
    def unread_count(self) -> int:
        return sum(1 for m in self.mails if m["unread"])

    def unread_mails(self) -> list[dict]:
        return [m for m in self.mails if m["unread"]]

    def sel_mail(self) -> dict | None:
        return next((m for m in self.mails if m["id"] == self.selected_mail),
                    self.mails[0] if self.mails else None)

    def select_mail(self, mid: str):
        # Opening a message marks it read, Gmail-style (decided 2026-07-16 —
        # replaced the 1s dwell timer; every selection here is a deliberate
        # click, there is no key-browsing to protect). Programmatic selection
        # in _set_mails never marks anything.
        self.selected_mail = mid
        self.auto_read(mid)
        self.fetch_body_html(mid)
        self.mails_changed.emit()

    def fetch_body_html(self, mid: str) -> None:
        """List rows arrive without HTML (kept light); the reading pane pulls
        one message's HTML on open. None stays None on failure → retried on
        the next open; '' means the message has no HTML part."""
        m = next((x for x in self.mails if x["id"] == mid), None)
        if m is None or m.get("body_html") is not None or self._data is None:
            return

        def handle(row):
            m2 = next((x for x in self.mails if x["id"] == mid), None)
            if m2 is not None:
                m2["body_html"] = (row or {}).get("body_html")
                self.mails_changed.emit()
        self._data.request("emails.get", {"id": mid}, handle)

    def _set_mails(self, result: dict) -> None:
        # emails.search responses carry only {"emails": [...]} — no status
        # keys — so absent keys must fall back to the PRIOR state, not a
        # reset default, or a search while disconnected would flip the
        # status to "connected"/"synced".
        self.mails = [_norm_mail(r) for r in result.get("emails", [])]
        self.mail_connected = result.get("connected", self.mail_connected)
        self.mail_syncing = result.get("syncing", self.mail_syncing)
        self.mail_last_sync = result.get("last_sync", self.mail_last_sync)
        self.mail_labels = result.get("labels", self.mail_labels)
        if (self.mail_scope not in ("inbox", "unread", "sent")
                and self.mail_scope not in self.mail_labels):
            self.mail_scope = "inbox"   # scope label vanished upstream
        counts = result.get("counts")
        if counts:
            self.mail_total = counts.get("total", self.mail_total)
        if self.selected_mail not in {m["id"] for m in self.mails}:
            self.selected_mail = self.mails[0]["id"] if self.mails else None
        if self.selected_mail is not None:
            self.fetch_body_html(self.selected_mail)   # pane shows it right away
        self.mails_changed.emit()

    def set_mail_scope(self, scope: str) -> None:
        if scope == self.mail_scope:
            return
        self.mail_scope = scope
        self.mail_suggestions.clear()
        if self.live:
            self.refresh_mails()
        else:
            self.mails_changed.emit()   # sample mode: chips reflect selection only

    def _scope_payload(self) -> dict:
        if self.mail_scope in ("inbox", "unread", "sent"):
            return {"filter": self.mail_scope}
        return {"filter": "label", "label": self.mail_scope}

    def refresh_mails(self) -> None:
        if self._data is not None:
            self._data.request("emails.list", self._scope_payload(),
                               self._set_mails)

    def refresh_inbox(self) -> None:
        """Manual refresh: delta-sync against Gmail, then reload the current
        scope (a label view reloads as itself)."""
        if self._data is not None:
            self._last_sync_req = time.monotonic()
            self._data.request("mail.refresh", self._scope_payload(),
                               self._set_mails)

    SYNC_DEBOUNCE_S = 60.0

    def sync_inbox(self) -> None:
        """Automatic refresh (app launch, Mail tab shown): a real Gmail
        delta-sync, debounced to one per minute so tab-flipping can't hammer
        Gmail. Inside the window it still re-reads the local mirror, so the
        view stays fresh either way."""
        now = time.monotonic()
        if (self._last_sync_req is not None
                and now - self._last_sync_req < self.SYNC_DEBOUNCE_S):
            self.refresh_mails()
        else:
            self.refresh_inbox()

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

    def delete_mail(self, mid: str) -> None:
        """Move to Gmail's Trash — the daemon gates it behind the confirm
        overlay before touching Gmail (same ritual as archive)."""
        if self._data is not None:
            self._data.request("emails.delete", {"id": mid}, self._mail_action_done)

    def set_mail_read(self, mid: str, read: bool) -> None:
        if self._data is not None:
            self._data.request("emails.mark_read", {"id": mid, "read": read},
                               self._mail_action_done)

    def auto_read(self, mid: str) -> None:
        """Open-as-read receipt: silent, ungated, flips the row locally so
        the dot clears immediately. Callers emit mails_changed themselves."""
        m = next((x for x in self.mails if x["id"] == mid), None)
        if m is None or not m["unread"]:
            return
        m["unread"] = False
        if self._data is not None:
            self._data.request("emails.auto_read", {"id": mid}, lambda _r: None)

    def suggest_labels(self, cb=None) -> None:
        """One explicit press → classify unlabeled inbox mail; results stay
        chips until tapped — nothing is written until accept."""
        if self._data is None:
            if cb:
                cb({})
            return

        def handle(result):
            self.mail_suggestions = dict((result or {}).get("suggestions", {}))
            self.mails_changed.emit()
            if cb:
                cb(result)
        self._data.request("mail.suggest_labels", {}, handle)

    def apply_label(self, mid: str, name: str) -> None:
        """Manual label pick from the reading pane (#9): the same
        emails.apply_label write the suggestion tap uses, but user-initiated.
        Toasts + reloads the scope so the moved-out message drops from inbox."""
        name = (name or "").strip()
        if not name:
            return
        if self._data is not None:
            self._data.request("emails.apply_label", {"id": mid, "label": name},
                               self._mail_action_done)
        else:
            m = next((x for x in self.mails if x["id"] == mid), None)
            if m is not None and name not in (m.get("label_names") or []):
                m.setdefault("label_names", []).append(name)
                if name not in self.mail_labels:
                    self.mail_labels = sorted(self.mail_labels + [name])
            self.mails_changed.emit()

    def remove_label(self, mid: str, name: str) -> None:
        """Remove a label from a message (#9). Wraps emails.remove_label."""
        name = (name or "").strip()
        if not name:
            return
        if self._data is not None:
            self._data.request("emails.remove_label", {"id": mid, "label": name},
                               self._mail_action_done)
        else:
            m = next((x for x in self.mails if x["id"] == mid), None)
            if m is not None and name in (m.get("label_names") or []):
                m["label_names"].remove(name)
            self.mails_changed.emit()

    def apply_suggestion(self, mid: str) -> None:
        name = self.mail_suggestions.pop(mid, None)
        if name is None:
            return
        if self._data is not None:
            self._data.request("emails.apply_label", {"id": mid, "label": name},
                               self._mail_action_done)
        else:
            self.mails_changed.emit()

    def reject_suggestion(self, mid: str) -> None:
        """Review-pass reject (suggest-labels v2): local only — nothing was
        ever written, so there is nothing to undo."""
        if self.mail_suggestions.pop(mid, None) is not None:
            self.mails_changed.emit()

    def dismiss_suggestions(self) -> None:
        """Clear the whole review pass without writing anything."""
        if self.mail_suggestions:
            self.mail_suggestions.clear()
            self.mails_changed.emit()

    def accept_all_for_label(self, label: str) -> None:
        """Per-label bulk accept from the review bar. Each accept is the same
        emails.apply_label write the single tap uses; one toast + refresh when
        the last one lands."""
        mids = [m for m, n in self.mail_suggestions.items() if n == label]
        for mid in mids:
            self.mail_suggestions.pop(mid, None)
        if not mids:
            return
        if self._data is None:
            self.mails_changed.emit()
            return
        left, filed = [len(mids)], [0]

        def done(res):
            left[0] -= 1
            if isinstance(res, dict) and res.get("ok"):
                filed[0] += 1
            if left[0] == 0:
                self.toast_requested.emit(
                    f"✓ Filed {filed[0]} under {label} — moved out of inbox")
                self.refresh_mails()

        for mid in mids:
            self._data.request("emails.apply_label",
                               {"id": mid, "label": label}, done)

    # ---- mail rules (2026-07-15) ----
    def open_rule_editor(self, prefill: dict | None = None) -> None:
        self.rule_edit_requested.emit(prefill or {})

    def list_rules(self, cb) -> None:
        if self._data is not None:
            self._data.request("rules.list", {}, cb)
        else:
            cb({"rules": [], "labels": []})

    def create_rule(self, rule: dict, cb=None) -> None:
        """Daemon gates creation behind the confirm overlay (summary + the
        apply-to-existing checkbox); Save pre-authorizes future auto-applies."""
        if self._data is not None:
            self._data.request("rules.create", {"rule": rule},
                               cb or (lambda _r: None))

    def update_rule(self, rid: int, rule: dict, cb=None) -> None:
        if self._data is not None:
            self._data.request("rules.update", {"id": rid, "rule": rule},
                               cb or (lambda _r: None))

    def delete_rule(self, rid: int, cb=None) -> None:
        if self._data is not None:
            self._data.request("rules.delete", {"id": rid},
                               cb or (lambda _r: None))

    def toggle_rule(self, rid: int, enabled: bool, cb=None) -> None:
        if self._data is not None:
            self._data.request("rules.toggle", {"id": rid, "enabled": enabled},
                               cb or (lambda _r: None))

    # ---- files workbench (new-features items 6-7) ----
    # Browsing, reading, and the user's own Save are UI-local (the filesystem
    # is native to this process; reads are ungated by explicit user decision,
    # and a manual Save is direct manipulation like adding a todo). Only the
    # model-involved calls — asks and edit proposals — go to the daemon.
    def list_dir(self, path) -> dict:
        from lumen.daemon.connectors import local_files
        return local_files.list_dir(path)

    def read_file(self, path) -> dict:
        from lumen.daemon.connectors import local_files
        return local_files.read_text(path)

    def save_file(self, path, content: str) -> dict:
        try:
            from pathlib import Path
            Path(path).expanduser().write_text(content)
        except OSError as e:
            return {"error": e.strerror or str(e)}
        return {"ok": True}

    def propose_edit(self, path: str, content: str, instruction: str, cb) -> None:
        """✎ Edit: one local-model generation over the current editor buffer;
        cb({ok, content|message}). Nothing is written until the user applies."""
        if self._data is not None:
            self._data.request("files.propose_edit",
                               {"path": path, "content": content,
                                "instruction": instruction}, cb)
        else:
            cb({"ok": False, "message": "Sample mode — edits need the daemon."})

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

    def fetch_briefing(self, cb) -> None:
        """cb({text}) — the collected morning briefing (Dashboard button)."""
        if self._data is not None:
            self._data.request("briefing.today", {}, cb)
        else:
            cb({"text": "Sample mode — the briefing needs the daemon running."})

    def refresh_manabi(self) -> None:
        """Japanese-study nudge for the dashboard: a cheap daemon-side
        read-only peek at Manabi's last-review signal."""
        if self._data is None:
            return

        def handle(result: dict) -> None:
            due = bool(result.get("due"))
            if due != self.manabi_due:
                self.manabi_due = due
                self.todos_changed.emit()      # repaint the dashboard column

        self._data.request("manabi.status", {}, handle)

    def update_event(self, event_id: str, calendar_id: str, changes: dict,
                     cb=None) -> None:
        """Edit an existing event (#12). The daemon gates the change behind the
        confirm overlay before patching Google Calendar. `changes` carries only
        the touched fields (title/start/end/all_day/location/color_id)."""
        if self._data is not None:
            self._data.request("calendar.update",
                               {"id": event_id, "calendar_id": calendar_id,
                                "changes": changes},
                               cb or (lambda _r: None))
        else:
            self.confirm_requested.emit({
                "icon": "▲", "title": "Update calendar event",
                "intro": "Lumen will change this event on your Google Calendar.",
                "rows": [("Title", changes.get("title", ""))],
                "confirm_label": "Save changes", "toast": "✓ Event updated"})

    def delete_event(self, event_id: str, calendar_id: str, cb=None) -> None:
        """External write: the daemon looks the event up in its cache and gates
        the delete behind the confirm overlay before touching Google Calendar."""
        if self._data is not None:
            self._data.request("calendar.delete",
                               {"id": event_id, "calendar_id": calendar_id},
                               cb or (lambda _r: None))
