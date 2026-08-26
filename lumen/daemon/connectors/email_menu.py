"""Email mirror: EmailStore (local full mirror the UI/chat read from) and
GmailSync (bounded bulk pull + History-API deltas via the API client — never
the LLM/MCP loop). See email-menu.md for the two-path strategy."""

import asyncio
import base64
import json
import logging
import re as _re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from html import unescape

from lumen.daemon.connectors import google_auth, mail_rules

log = logging.getLogger(__name__)

HISTORY_KEY = "gmail_history_id"
CURSOR_KEY = "gmail_bulk_cursor"
WINDOW_KEY = "gmail_bulk_window_months"
LAST_SYNC_KEY = "gmail_last_sync"
RUN_KEY = "gmail_rebaseline_run"

_COLS = ("id", "thread_id", "sender", "recipients", "subject", "body",
         "body_html", "snippet", "labels", "received_at", "is_read",
         "attachments", "last_seen")
# List/search responses skip body_html — 50 raw-HTML bodies per IPC page is
# dead weight; the reading pane fetches one message's HTML via emails.get.
_LIST_COLS = ", ".join(c for c in _COLS if c != "body_html")


def _decode(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    text = _re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=_re.S | _re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    return _re.sub(r"\s+", " ", unescape(text)).strip()


def _walk_body(payload: dict) -> tuple[str, str, list[str]]:
    """Depth-first: collect attachment filenames; body is the first text/plain
    part, else the first text/html part stripped; the raw HTML is kept too so
    the reading pane can render the message properly."""
    plain, html, attachments = [], [], []

    def walk(part: dict) -> None:
        if part.get("filename"):
            attachments.append(part["filename"])
        data = part.get("body", {}).get("data")
        mime = part.get("mimeType", "")
        if data and mime.startswith("text/plain") and not part.get("filename"):
            plain.append(_decode(data))
        elif data and mime.startswith("text/html") and not part.get("filename"):
            html.append(_decode(data))
        for child in part.get("parts", []):
            walk(child)

    walk(payload)
    body = "\n".join(plain) if plain else _strip_html("\n".join(html))
    return body, "\n".join(html), attachments


def normalize_message(raw: dict) -> dict:
    headers = {h["name"].lower(): h["value"]
               for h in raw.get("payload", {}).get("headers", [])}
    body, body_html, attachments = _walk_body(raw.get("payload", {}))
    labels = list(raw.get("labelIds", []))
    received = datetime.fromtimestamp(
        int(raw.get("internalDate", 0)) / 1000, tz=timezone.utc)
    return {"id": raw["id"], "thread_id": raw.get("threadId"),
            "sender": headers.get("from", ""), "recipients": headers.get("to", ""),
            "subject": headers.get("subject", ""), "body": body,
            "body_html": body_html,
            "snippet": raw.get("snippet", ""), "labels": labels,
            "received_at": received.isoformat(timespec="seconds"),
            "is_read": "UNREAD" not in labels, "attachments": attachments}


# ---- mail query parsing (search_email tool + UI search box) ----------------
# The local 4B model, trained on Gmail, emits operator queries (from:me,
# subject:'x', on:2026-07-08, after:/before:, boolean ||) and asks for "the most
# recent email". The old FTS-only search fed all of that to MATCH as literal
# tokens, so operator queries returned nothing and "most recent" returned a
# bm25-ranked hit from months ago (live fabrication 2026-07-17, todo-fixes
# #11/#13). parse_mail_query meets the model where it is: it pulls the operators
# out into structured filters (date filters are the USER's LOCAL day, converted
# to the UTC received_at is stored in), leaves the rest as free text, and the
# store orders every result newest-first so recency questions just work.
_QUERY_OP = _re.compile(r"""(\w+):(?:"([^"]*)"|'([^']*)'|(\S+))""")
_FIELD = {
    "from": "sender", "sender": "sender",
    "to": "recipients", "recipient": "recipients", "recipients": "recipients",
    "cc": "recipients",
    "subject": "subject", "title": "subject",
    "body": "body",
    "after": "after", "since": "after",
    "before": "before", "until": "before",
    "on": "on", "date": "on", "received": "on", "day": "on",
    "newer_than": "newer", "older_than": "older",
    "in": "scope", "label": "label", "is": "flag",
}
_ME = frozenset({"me", "myself", "i", "self"})
_BOOL_JUNK = _re.compile(r"\|\||&&|[|&()]|\b(?:AND|OR|NOT)\b")
_MONTHS = {m: i for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"),
     ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
     ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
     ("nov", "november"), ("dec", "december")], start=1) for m in names}


def _parse_date(s: str, today: date) -> date | None:
    """A date the model or user wrote → a calendar date. ISO/slash formats,
    today/yesterday, and month-name shapes ('July 8', '8th of July')."""
    s = s.strip().strip(",")
    low = s.lower()
    if low == "today":
        return today
    if low == "yesterday":
        return today - timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    month = day = year = None
    for t in _re.findall(r"[a-z]+|\d+", low):
        if t in _MONTHS:
            month = _MONTHS[t]
        elif t.isdigit():
            n = int(t)
            if n > 31:
                year = n
            elif day is None:
                day = n
    if month and day:
        try:
            return date(year or today.year, month, day)
        except ValueError:
            return None
    return None


def _parse_ndays(s: str) -> int | None:
    m = _re.match(r"(\d+)\s*([dwmy]?)", s.strip().lower())
    return int(m.group(1)) * {"": 1, "d": 1, "w": 7, "m": 30, "y": 365}[m.group(2)] \
        if m else None


def parse_mail_query(query: str, *, now_local: datetime) -> dict:
    """A Gmail-ish query string → structured filters + free-text terms. Dates
    are resolved in `now_local`'s timezone and returned as UTC ISO bounds (the
    format received_at is stored in), so 'on:2026-07-08' matches the messages
    the user saw on their July 8, not the UTC calendar day."""
    tz, today = now_local.tzinfo, now_local.date()

    def day_bounds(d: date) -> tuple[str, str]:
        start = datetime(d.year, d.month, d.day, tzinfo=tz)
        return (start.astimezone(timezone.utc).isoformat(timespec="seconds"),
                (start + timedelta(days=1)).astimezone(timezone.utc)
                .isoformat(timespec="seconds"))

    def rel(days: int) -> str:
        return (now_local - timedelta(days=days)).astimezone(
            timezone.utc).isoformat(timespec="seconds")

    f: dict = {"terms": [], "sender": None, "recipients": None, "subject": None,
               "after": None, "before": None, "scope": None, "unread": None}
    leftover, pos = [], 0
    for m in _QUERY_OP.finditer(query or ""):
        leftover.append((query or "")[pos:m.start()])
        pos = m.end()
        field = _FIELD.get(m.group(1).lower())
        val = (m.group(2) or m.group(3) or m.group(4) or "").strip("*").strip()
        if field is None:
            leftover.append(val)          # unknown operator → keep the value as free text
            continue
        if not val:
            continue
        if field == "sender":
            f["scope"] = "sent" if val.lower() in _ME else f["scope"]
            f["sender"] = None if val.lower() in _ME else val
        elif field == "recipients":
            f["recipients"] = None if val.lower() in _ME else val
        elif field == "subject":
            f["subject"] = val
        elif field == "body":
            leftover.append(val)          # body words are just free-text search
        elif field in ("after", "before", "on"):
            d = _parse_date(val, today)
            if d is not None:
                s, e = day_bounds(d)
                if field == "on":
                    f["after"], f["before"] = s, e
                elif field == "after":
                    f["after"] = s
                else:
                    f["before"] = s
        elif field == "newer" and (n := _parse_ndays(val)) is not None:
            f["after"] = rel(n)
        elif field == "older" and (n := _parse_ndays(val)) is not None:
            f["before"] = rel(n)
        elif field == "scope":
            f["scope"] = {"sent": "sent", "inbox": "inbox"}.get(val.lower(), f["scope"])
        elif field == "flag":
            f["unread"] = {"unread": True, "read": False}.get(val.lower(), f["unread"])
        # label: is accepted (so it doesn't pollute FTS) but not filtered on
    leftover.append((query or "")[pos:])
    text = _BOOL_JUNK.sub(" ", " ".join(leftover)).replace('"', " ") \
        .replace("'", " ").replace("*", " ")
    f["terms"] = text.split()
    return f


def _like_arg(v: str) -> str:
    esc = v.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{esc}%"


class EmailStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert(self, msgs: list[dict]) -> None:
        # UPSERT, never INSERT OR REPLACE — REPLACE bypasses the delete trigger
        # and silently corrupts the external-content FTS index.
        sets = ", ".join(f"{c} = excluded.{c}" for c in _COLS if c != "id")
        with self._conn:
            self._conn.executemany(
                f"INSERT INTO emails ({', '.join(_COLS)}) "
                f"VALUES ({', '.join('?' * len(_COLS))}) "
                f"ON CONFLICT(id) DO UPDATE SET {sets}",
                [tuple(
                    ",".join(m["labels"]) if c == "labels"
                    else json.dumps(m.get("attachments", [])) if c == "attachments"
                    else int(bool(m["is_read"])) if c == "is_read"
                    else m.get(c)
                    for c in _COLS) for m in msgs])

    def get(self, mid: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM emails WHERE id = ?", (mid,)).fetchone()
        return self._to_dict(row) if row else None

    def list_page(self, filter: str = "inbox", limit: int = 50, offset: int = 0,
                  label_id: str | None = None) -> list[dict]:
        # Each label chip is its own inbox (design 2026-07-16): "inbox" is the
        # default one — INBOX mail carrying no user label — so mail labeled
        # upstream without being archived still leaves the default view.
        # "unread" stays inbox-scoped (2026-07-15).
        in_inbox = ("(',' || labels || ',') LIKE '%,INBOX,%' AND NOT EXISTS "
                    "(SELECT 1 FROM gmail_labels gl WHERE gl.type = 'user' "
                    "AND (',' || emails.labels || ',') LIKE '%,' || gl.id || ',%')")
        where, params = {
            "inbox": (f"WHERE {in_inbox}", ()),
            "unread": (f"WHERE is_read = 0 AND {in_inbox}", ()),
            "sent": ("WHERE (',' || labels || ',') LIKE '%,SENT,%'", ()),
            "label": ("WHERE (',' || labels || ',') LIKE ?", (f"%,{label_id},%",)),
            "all": ("", ()),
        }[filter]
        rows = self._conn.execute(
            f"SELECT {_LIST_COLS} FROM emails {where} ORDER BY received_at DESC, id "
            f"LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        return [self._to_dict(r) for r in rows]

    def search(self, query: str, limit: int = 50,
               now_local: datetime | None = None) -> list[dict]:
        """Newest-first search over the mirror. Understands the operator/date
        queries the model emits (from:/to:/subject:/on:/after:/before:/
        newer_than:/in:/is:) as structured filters, resolving dates in the
        user's local timezone; the rest is FTS free text. An empty query (or
        one that is only filters) returns the most recent matching messages —
        so 'most recent email' and 'email on the 8th' both work."""
        p = parse_mail_query(query, now_local=now_local or datetime.now().astimezone())
        where, params = [], []
        if p["after"]:
            where.append("e.received_at >= ?"); params.append(p["after"])
        if p["before"]:
            where.append("e.received_at < ?"); params.append(p["before"])
        if p["sender"]:
            where.append("e.sender LIKE ? ESCAPE '\\'"); params.append(_like_arg(p["sender"]))
        if p["recipients"]:
            where.append("e.recipients LIKE ? ESCAPE '\\'"); params.append(_like_arg(p["recipients"]))
        if p["subject"]:
            where.append("e.subject LIKE ? ESCAPE '\\'"); params.append(_like_arg(p["subject"]))
        if p["scope"] == "sent":
            where.append("(',' || e.labels || ',') LIKE '%,SENT,%'")
        elif p["scope"] == "inbox":
            where.append("(',' || e.labels || ',') LIKE '%,INBOX,%'")
        if p["unread"] is True:
            where.append("e.is_read = 0")
        elif p["unread"] is False:
            where.append("e.is_read = 1")
        cols = ", ".join(f"e.{c}" for c in _COLS if c != "body_html")
        if p["terms"]:
            # FTS filters the candidate set; ORDER BY recency, not bm25 — an
            # assistant answering "latest email from X" wants the newest match.
            match = " ".join(f'"{t}"' for t in p["terms"])
            clause = " AND ".join(["emails_fts MATCH ?", *where])
            rows = self._conn.execute(
                f"SELECT {cols} FROM emails_fts f JOIN emails e ON e.rowid = f.rowid "
                f"WHERE {clause} ORDER BY e.received_at DESC, e.id LIMIT ?",
                (match, *params, limit)).fetchall()
        else:
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            rows = self._conn.execute(
                f"SELECT {cols} FROM emails e{clause} "
                f"ORDER BY e.received_at DESC, e.id LIMIT ?",
                (*params, limit)).fetchall()
        return [self._to_dict(r) for r in rows]

    def unread(self, limit: int = 10) -> list[dict]:
        return self.list_page("unread", limit=limit)

    def involving(self, addr: str, limit: int = 5) -> list[dict]:
        """Messages to or from `addr`, newest first — meeting-prep retrieval
        is deterministic (the FTS index doesn't cover addresses)."""
        esc = (addr.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_"))
        pat = f"%{esc}%"
        rows = self._conn.execute(
            "SELECT * FROM emails WHERE sender LIKE ? ESCAPE '\\' "
            "OR recipients LIKE ? ESCAPE '\\' "
            "ORDER BY received_at DESC, id LIMIT ?", (pat, pat, limit)).fetchall()
        return [self._to_dict(r) for r in rows]

    def sent(self, since_iso: str, limit: int = 20) -> list[dict]:
        """SENT messages after `since_iso`, oldest first — the commitment
        scan walks forward so its cursor only ever advances."""
        rows = self._conn.execute(
            "SELECT * FROM emails WHERE (',' || labels || ',') LIKE '%,SENT,%' "
            "AND received_at > ? ORDER BY received_at, id LIMIT ?",
            (since_iso, limit)).fetchall()
        return [self._to_dict(r) for r in rows]

    def delete(self, mids: list[str]) -> None:
        with self._conn:
            self._conn.executemany("DELETE FROM emails WHERE id = ?",
                                   [(m,) for m in mids])

    def update_labels(self, mid: str, add: list[str], remove: list[str]) -> None:
        row = self._conn.execute("SELECT labels FROM emails WHERE id = ?", (mid,)).fetchone()
        if row is None:
            return
        labels = [l for l in (row["labels"] or "").split(",") if l and l not in remove]
        labels += [l for l in add if l not in labels]
        with self._conn:
            self._conn.execute(
                "UPDATE emails SET labels = ?, is_read = ? WHERE id = ?",
                (",".join(labels), int("UNREAD" not in labels), mid))

    def set_body_html(self, mid: str, html: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE emails SET body_html = ? WHERE id = ?",
                               (html, mid))

    def set_labels(self, rows: list[dict]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM gmail_labels")
            self._conn.executemany(
                "INSERT INTO gmail_labels (id, name, type) VALUES (?, ?, ?)",
                [(r["id"], r["name"], r.get("type", "user")) for r in rows])

    def upsert_label(self, lid: str, name: str, type_: str = "user") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO gmail_labels (id, name, type) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name",
                (lid, name, type_))

    def labels_map(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT id, name FROM gmail_labels").fetchall()
        return {r["id"]: r["name"] for r in rows}

    def user_labels(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, name FROM gmail_labels WHERE type = 'user' "
            "ORDER BY name COLLATE NOCASE").fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]

    def label_id(self, name: str) -> str | None:
        labels = self.user_labels()
        for l in labels:
            if l["name"] == name:
                return l["id"]
        want = name.casefold()
        return next((l["id"] for l in labels
                     if l["name"].casefold() == want), None)

    def present_label_ids(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT labels FROM emails WHERE labels != ''").fetchall()
        return {l for r in rows for l in (r["labels"] or "").split(",") if l}

    def prune_not_seen(self, run_id: str, since_iso: str) -> int:
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM emails WHERE received_at >= ? "
                "AND (last_seen IS NULL OR last_seen != ?)", (since_iso, run_id))
        return cur.rowcount

    def counts(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN is_read = 0 THEN 1 END), 0) AS unread "
            "FROM emails").fetchone()
        return {"total": row["total"], "unread": row["unread"]}

    def get_state(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str | None) -> None:
        with self._conn:
            if value is None:
                self._conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                    (key, value))

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["labels"] = [l for l in (d["labels"] or "").split(",") if l]
        d["attachments"] = json.loads(d["attachments"] or "[]")
        d["is_read"] = bool(d["is_read"])
        return d


class GmailSync:
    """Bounded bulk pull then History-API deltas, via the API client on a
    timer — the poller must never wake the LLM (email-menu.md NOT #1)."""

    PAGE_SIZE = 100

    def __init__(self, store: EmailStore, google_cfg, sync_cfg, *,
                 service_factory=None, rules=None):
        self._store = store
        self._google = google_cfg
        self._sync = sync_cfg
        self._rules = rules         # RuleStore — deterministic, applied on sync
        self._injected = service_factory is not None
        self._service_factory = service_factory or self._build_service
        self._sync_lock = asyncio.Lock()
        # Runtime Disable switch (#38); __main__ wires it to ConnectionState.
        self.paused = lambda: False

    @property
    def connected(self) -> bool:
        return google_auth.connected(self._google, google_auth.GMAIL_READ_SCOPES)

    @property
    def syncing(self) -> bool:
        return self._store.get_state(CURSOR_KEY) is not None

    @property
    def busy(self) -> bool:
        return self._sync_lock.locked()

    def last_sync(self) -> str | None:
        return self._store.get_state(LAST_SYNC_KEY)

    def _build_service(self, write: bool = False):
        scopes = (google_auth.GMAIL_WRITE_SCOPES if write
                  else google_auth.GMAIL_READ_SCOPES)
        creds = google_auth.load_credentials(self._google, scopes)
        if creds is None:
            return None
        from googleapiclient.discovery import build
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _window_start(self) -> date:
        return date.today() - timedelta(days=self._sync.gmail_window_months * 30)

    async def sync_once(self) -> bool:
        async with self._sync_lock:
            return await self._sync_once_locked()

    async def _sync_once_locked(self) -> bool:
        try:
            service = self._service_factory()
        except Exception:
            log.exception("could not build gmail service")
            return False
        if service is None:
            return False   # not connected yet — a normal state
        await self.refresh_labels(service)   # name↔id map rides every sync
        if self._store.get_state(HISTORY_KEY) is None or self.syncing:
            return await self._bulk(service)
        return await self._incremental(service)

    async def _bulk(self, service) -> bool:
        run_id = self._store.get_state(RUN_KEY)
        if self._store.get_state(CURSOR_KEY) is None:
            # Pending sentinel: marks bulk-in-progress from entry, before the
            # first page even attempts, so a page-1 failure still resumes as
            # BULK on the next sync_once instead of falling through to
            # _incremental with no cursor and no fetched backlog.
            self._store.set_state(CURSOR_KEY, "")
        cursor = self._store.get_state(CURSOR_KEY) or None
        if self._store.get_state(HISTORY_KEY) is None:
            # Capture the position BEFORE listing: anything that changes during
            # the pull is then covered by the first incremental sync.
            try:
                profile = await asyncio.to_thread(
                    lambda: service.users().getProfile(userId="me").execute())
            except Exception:
                log.exception("gmail getProfile failed")
                return False
            self._store.set_state(HISTORY_KEY, str(profile["historyId"]))
        after = self._window_start().strftime("%Y/%m/%d")
        while True:
            try:
                msgs, next_cursor = await asyncio.to_thread(
                    self._bulk_page_blocking, service, after, cursor)
            except Exception:
                log.exception("gmail bulk page failed — cursor kept for resume")
                return False
            if run_id:
                for m in msgs:
                    m["last_seen"] = run_id
            self._store.upsert(msgs)                       # loop thread — single writer
            self._store.set_state(CURSOR_KEY, next_cursor)  # resumable after EVERY page
            cursor = next_cursor
            if cursor is None:
                break
        if run_id:
            pruned = self._store.prune_not_seen(
                run_id, self._window_start().isoformat() + "T00:00:00+00:00")
            log.info("gmail re-baseline pruned %d rows", pruned)
            self._store.set_state(RUN_KEY, None)
        self._store.set_state(LAST_SYNC_KEY,
                              datetime.now().isoformat(timespec="seconds"))
        return True

    def _bulk_page_blocking(self, service, after: str, cursor: str | None):
        resp = service.users().messages().list(
            userId="me", q=f"after:{after}", maxResults=self.PAGE_SIZE,
            pageToken=cursor, includeSpamTrash=False).execute()
        msgs = [normalize_message(
                    service.users().messages().get(userId="me", id=ref["id"],
                                                   format="full").execute())
                for ref in resp.get("messages", [])]
        return msgs, resp.get("nextPageToken")

    @staticmethod
    def _is_404(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "resp", None), "status", None)
        return status in (404, "404")

    def _fetch_full_blocking(self, service, ids: list[str]) -> list[dict] | None:
        """Blocking: fetch each id individually so one bad id can't sink the
        page. A 404 means the message vanished upstream (already-gone add) —
        skip it. Any other error aborts the whole fetch (None) so the caller
        can retry the window on the next poll instead of losing the batch."""
        msgs = []
        for i in ids:
            try:
                raw = service.users().messages().get(
                    userId="me", id=i, format="full").execute()
            except Exception as e:
                if self._is_404(e):
                    continue
                log.exception("gmail incremental fetch failed for id=%s", i)
                return None
            msgs.append(normalize_message(raw))
        return msgs

    async def _incremental(self, service) -> bool:
        start = self._store.get_state(HISTORY_KEY)
        page_token, latest = None, None
        deleted_ids: set[str] = set()   # run-wide: earlier/same-page deletes
        while True:
            try:
                resp = await asyncio.to_thread(
                    lambda: service.users().history().list(
                        userId="me", startHistoryId=start,
                        pageToken=page_token).execute())
            except Exception as e:
                if self._is_404(e):
                    # Expired history position — re-baseline the bounded window
                    # (a normal long-offline outcome, not an error).
                    log.info("gmail historyId expired — re-baselining")
                    self._store.set_state(RUN_KEY, str(uuid.uuid4()))
                    self._store.set_state(HISTORY_KEY, None)
                    self._store.set_state(CURSOR_KEY, None)
                    return await self._bulk(service)
                log.exception("gmail incremental sync failed")
                return False
            latest = resp.get("historyId", latest)
            added_ids = []
            seen_added = set()
            for h in resp.get("history", []):
                for m in h.get("messagesAdded", []):
                    mid = m["message"]["id"]
                    if mid not in seen_added:
                        seen_added.add(mid)
                        added_ids.append(mid)
                del_ids = [m["message"]["id"] for m in h.get("messagesDeleted", [])]
                deleted_ids.update(del_ids)
                self._store.delete(del_ids)
                for change in h.get("labelsAdded", []):
                    self._store.update_labels(change["message"]["id"],
                                              add=change.get("labelIds", []), remove=[])
                for change in h.get("labelsRemoved", []):
                    self._store.update_labels(change["message"]["id"], add=[],
                                              remove=change.get("labelIds", []))
            # Drop ids added-then-purged within this run (same page or an
            # earlier one) before fetching — otherwise they resurrect as
            # zombie rows.
            ids = [i for i in added_ids if i not in deleted_ids]
            if ids:
                msgs = await asyncio.to_thread(self._fetch_full_blocking, service, ids)
                if msgs is None:
                    # Non-404 fetch failure: don't advance HISTORY_KEY, retry
                    # this identical window on the next poll.
                    return False
                self._store.upsert(msgs)
                await self._apply_rules(msgs)   # new mail only — never the bulk pull
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        if latest is not None:
            self._store.set_state(HISTORY_KEY, str(latest))
        self._store.set_state(LAST_SYNC_KEY,
                              datetime.now().isoformat(timespec="seconds"))
        return True

    async def poll_forever(self) -> None:
        """Daemon background task; cancellation is the shutdown path."""
        interval = self._sync.gmail_poll_minutes * 60
        while True:
            await self._poll_iteration()
            await asyncio.sleep(interval)

    async def _poll_iteration(self) -> None:
        # `paused` is the runtime Disable switch (#38): when the connection is
        # turned off in Settings we skip the sync but keep the loop alive so a
        # re-enable resumes on the next tick without restarting the daemon.
        if self.paused():
            return
        try:
            await self.sync_once()
        except Exception:
            log.exception("gmail poll iteration failed")

    async def fetch_html(self, mid: str) -> str | None:
        """Lazy HTML backfill: mail mirrored before the body_html column gets
        its HTML on first open — one read-scope fetch, cached forever ('' when
        the message simply has no HTML part)."""
        try:
            service = self._service_factory()
        except Exception:
            log.exception("could not build gmail service")
            return None
        if service is None:
            return None
        try:
            raw = await asyncio.to_thread(
                lambda: service.users().messages().get(
                    userId="me", id=mid, format="full").execute())
        except Exception:
            log.exception("gmail html fetch failed for %s", mid)
            return None
        _body, html, _atts = _walk_body(raw.get("payload", {}))
        self._store.set_body_html(mid, html)
        return html

    async def _modify(self, mid: str, body: dict) -> bool:
        """Router (Task 9) only calls archive/mark_read AFTER a user confirm."""
        try:
            service = (self._service_factory() if self._injected
                       else self._build_service(write=True))
        except Exception:
            log.exception("could not build gmail service")
            return False
        if service is None:
            return False
        try:
            await asyncio.to_thread(
                lambda: service.users().messages().modify(
                    userId="me", id=mid, body=body).execute())
        except Exception:
            log.exception("gmail modify failed for %s", mid)
            return False
        return True

    async def archive(self, mid: str) -> bool:
        if not await self._modify(mid, {"removeLabelIds": ["INBOX"]}):
            return False
        self._store.update_labels(mid, add=[], remove=["INBOX"])
        return True

    async def trash(self, mid: str) -> bool:
        """Move to Gmail's Trash (recoverable there ~30 days) and drop the
        mirror row — the mirror holds non-trash mail only, matching the bulk
        pull's includeSpamTrash=False. Only ever called downstream of the
        emails.delete confirm gate."""
        try:
            service = (self._service_factory() if self._injected
                       else self._build_service(write=True))
        except Exception:
            log.exception("could not build gmail service")
            return False
        if service is None:
            return False
        try:
            await asyncio.to_thread(
                lambda: service.users().messages().trash(
                    userId="me", id=mid).execute())
        except Exception:
            log.exception("gmail trash failed for %s", mid)
            return False
        self._store.delete([mid])
        return True

    async def refresh_labels(self, service=None) -> bool:
        """Cache the Gmail label name↔id map locally; failures never sink a
        sync (labels just go stale until the next poll)."""
        try:
            service = service or self._service_factory()
            if service is None:
                return False
            resp = await asyncio.to_thread(
                lambda: service.users().labels().list(userId="me").execute())
        except Exception:
            log.exception("gmail labels.list failed")
            return False
        self._store.set_labels([{"id": l["id"], "name": l.get("name", ""),
                                 "type": l.get("type", "user")}
                                for l in resp.get("labels", [])])
        return True

    async def create_label(self, name: str) -> str | None:
        try:
            service = (self._service_factory() if self._injected
                       else self._build_service(write=True))
        except Exception:
            log.exception("could not build gmail service")
            return None
        if service is None:
            return None
        try:
            created = await asyncio.to_thread(
                lambda: service.users().labels().create(
                    userId="me", body={"name": name}).execute())
        except Exception:
            # 409 = the name already exists upstream (stale local map):
            # re-pull the map and use the existing id.
            await self.refresh_labels()
            return self._store.label_id(name)
        self._store.upsert_label(created["id"], name)
        return created["id"]

    async def apply_label(self, mid: str, label_name: str) -> bool:
        """Label = move (design 2026-07-15): add the label, remove INBOX —
        Gmail first, then the mirror. Creates the Gmail label if missing."""
        label_id = self._store.label_id(label_name)
        if label_id is None:
            label_id = await self.create_label(label_name)
        if label_id is None:
            return False
        if not await self._modify(mid, {"addLabelIds": [label_id],
                                        "removeLabelIds": ["INBOX"]}):
            return False
        self._store.update_labels(mid, add=[label_id], remove=["INBOX"])
        return True

    async def remove_label(self, mid: str, label_name: str) -> bool:
        """Take a user label off a message and return it to the Inbox (#42) —
        the exact inverse of apply_label (label = move out of inbox). Removing
        the label un-files the message, so it reappears in the inbox. Gmail
        first, then the mirror."""
        label_id = self._store.label_id(label_name)
        if label_id is None:
            return False
        if not await self._modify(mid, {"removeLabelIds": [label_id],
                                        "addLabelIds": ["INBOX"]}):
            return False
        self._store.update_labels(mid, add=["INBOX"], remove=[label_id])
        return True

    async def _apply_rules(self, msgs: list[dict]) -> None:
        """Deterministic pass over newly-arrived mail — no LLM, ever, here.
        A failed apply just leaves the message in the inbox; never fails
        the sync."""
        rules = self._rules.enabled() if self._rules is not None else []
        if not rules:
            return
        for m in msgs:
            if "INBOX" not in m["labels"] or "SENT" in m["labels"]:
                continue
            for name in mail_rules.matching_labels(rules, m):
                try:
                    await self.apply_label(m["id"], name)
                except Exception:
                    log.exception("rule apply failed for %s", m["id"])

    async def mark_read(self, mid: str, read: bool) -> bool:
        body = ({"removeLabelIds": ["UNREAD"]} if read
                else {"addLabelIds": ["UNREAD"]})
        if not await self._modify(mid, body):
            return False
        if read:
            self._store.update_labels(mid, add=[], remove=["UNREAD"])
        else:
            self._store.update_labels(mid, add=["UNREAD"], remove=[])
        return True

    async def send(self, to: list[str], cc: list[str], bcc: list[str],
                   subject: str, body: str, *, reply_to: str | None = None) -> bool:
        """Send via the Gmail API — only ever called downstream of the compose
        popup's explicit Send click (the popup is the write confirmation).
        gmail.modify, already granted, authorizes send — no new scope.
        reply_to is a mirrored message id: its thread_id plus a metadata fetch
        of the original's Message-ID make the reply thread properly."""
        try:
            service = (self._service_factory() if self._injected
                       else self._build_service(write=True))
        except Exception:
            log.exception("could not build gmail service")
            return False
        if service is None:
            return False
        thread_id = None
        if reply_to is not None:
            row = self._store.get(reply_to)
            thread_id = (row or {}).get("thread_id")

        def blocking():
            msg = EmailMessage()
            msg["To"] = ", ".join(to)
            if cc:
                msg["Cc"] = ", ".join(cc)
            if bcc:
                msg["Bcc"] = ", ".join(bcc)
            msg["Subject"] = subject
            if reply_to is not None:
                try:
                    meta = service.users().messages().get(
                        userId="me", id=reply_to, format="metadata",
                        metadataHeaders=["Message-ID"]).execute()
                    orig = next((h["value"] for h in
                                 meta.get("payload", {}).get("headers", [])
                                 if h.get("name", "").lower() == "message-id"), None)
                except Exception:
                    orig = None          # thread via threadId alone
                if orig:
                    msg["In-Reply-To"] = orig
                    msg["References"] = orig
            msg.set_content(body)
            payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
            if thread_id:
                payload["threadId"] = thread_id
            service.users().messages().send(userId="me", body=payload).execute()

        try:
            await asyncio.to_thread(blocking)
        except Exception:
            log.exception("gmail send failed")
            return False
        return True
