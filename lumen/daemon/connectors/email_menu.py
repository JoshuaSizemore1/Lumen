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
from html import unescape

from lumen.daemon.connectors import google_auth

log = logging.getLogger(__name__)

HISTORY_KEY = "gmail_history_id"
CURSOR_KEY = "gmail_bulk_cursor"
WINDOW_KEY = "gmail_bulk_window_months"
LAST_SYNC_KEY = "gmail_last_sync"
RUN_KEY = "gmail_rebaseline_run"

_COLS = ("id", "thread_id", "sender", "recipients", "subject", "body", "snippet",
         "labels", "received_at", "is_read", "attachments", "last_seen")


def _decode(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    text = _re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=_re.S | _re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    return _re.sub(r"\s+", " ", unescape(text)).strip()


def _walk_body(payload: dict) -> tuple[str, list[str]]:
    """Depth-first: collect attachment filenames; body is the first text/plain
    part, else the first text/html part stripped."""
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
    return body, attachments


def normalize_message(raw: dict) -> dict:
    headers = {h["name"].lower(): h["value"]
               for h in raw.get("payload", {}).get("headers", [])}
    body, attachments = _walk_body(raw.get("payload", {}))
    labels = list(raw.get("labelIds", []))
    received = datetime.fromtimestamp(
        int(raw.get("internalDate", 0)) / 1000, tz=timezone.utc)
    return {"id": raw["id"], "thread_id": raw.get("threadId"),
            "sender": headers.get("from", ""), "recipients": headers.get("to", ""),
            "subject": headers.get("subject", ""), "body": body,
            "snippet": raw.get("snippet", ""), "labels": labels,
            "received_at": received.isoformat(timespec="seconds"),
            "is_read": "UNREAD" not in labels, "attachments": attachments}


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

    def list_page(self, filter: str = "inbox", limit: int = 50, offset: int = 0) -> list[dict]:
        where = {"inbox": "WHERE (',' || labels || ',') LIKE '%,INBOX,%'",
                 "unread": "WHERE is_read = 0",
                 "all": ""}[filter]
        rows = self._conn.execute(
            f"SELECT * FROM emails {where} ORDER BY received_at DESC, id "
            f"LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [self._to_dict(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        # Quote each token so user input can't be parsed as FTS5 syntax.
        q = " ".join(f'"{t}"' for t in (query or "").replace('"', " ").split())
        if not q:
            return []
        rows = self._conn.execute(
            "SELECT e.* FROM emails_fts f JOIN emails e ON e.rowid = f.rowid "
            "WHERE emails_fts MATCH ? ORDER BY bm25(emails_fts) LIMIT ?",
            (q, limit)).fetchall()
        return [self._to_dict(r) for r in rows]

    def unread(self, limit: int = 10) -> list[dict]:
        return self.list_page("unread", limit=limit)

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

    def __init__(self, store: EmailStore, google_cfg, sync_cfg, *, service_factory=None):
        self._store = store
        self._google = google_cfg
        self._sync = sync_cfg
        self._service_factory = service_factory or self._build_service

    @property
    def connected(self) -> bool:
        return google_auth.connected(self._google, google_auth.GMAIL_READ_SCOPES)

    @property
    def syncing(self) -> bool:
        return self._store.get_state(CURSOR_KEY) is not None

    def last_sync(self) -> str | None:
        return self._store.get_state(LAST_SYNC_KEY)

    def _build_service(self):
        creds = google_auth.load_credentials(self._google, google_auth.GMAIL_READ_SCOPES)
        if creds is None:
            return None
        from googleapiclient.discovery import build
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _window_start(self) -> date:
        return date.today() - timedelta(days=self._sync.gmail_window_months * 30)

    async def sync_once(self) -> bool:
        try:
            service = self._service_factory()
        except Exception:
            log.exception("could not build gmail service")
            return False
        if service is None:
            return False   # not connected yet — a normal state
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
            try:
                await self.sync_once()
            except Exception:
                log.exception("gmail poll iteration failed")
            await asyncio.sleep(interval)
