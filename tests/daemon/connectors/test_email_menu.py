"""EmailStore: mirror reads/writes over the Task-1 schema."""
import base64

from lumen.daemon import db
from lumen.daemon.connectors.email_menu import EmailStore, normalize_message


def make_store(tmp_path):
    return EmailStore(db.connect(tmp_path / "e.db"))


def msg(i, **over):
    base = {"id": f"m{i}", "thread_id": f"t{i}", "sender": f"Sender {i} <s{i}@x.com>",
            "recipients": "me@x.com", "subject": f"Subject {i}",
            "body": f"body text {i}", "snippet": f"snip {i}",
            "labels": ["INBOX", "UNREAD"], "received_at": f"2026-07-{i:02d}T10:00:00+00:00",
            "is_read": False, "attachments": []}
    base.update(over)
    return base


def test_upsert_get_roundtrip_and_update(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, attachments=["a.pdf"])])
    got = store.get("m1")
    assert got["labels"] == ["INBOX", "UNREAD"] and got["attachments"] == ["a.pdf"]
    assert got["is_read"] is False
    store.upsert([msg(1, subject="Edited", labels=["INBOX"], is_read=True)])
    got = store.get("m1")
    assert got["subject"] == "Edited" and got["is_read"] is True
    assert store.get("nope") is None


def test_list_page_filters_and_order(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1), msg(2, labels=["UNREAD"]),           # archived (no INBOX)
                  msg(3, labels=["INBOX"], is_read=True)])
    inbox = store.list_page("inbox")
    assert [m["id"] for m in inbox] == ["m3", "m1"]            # newest first
    assert [m["id"] for m in store.list_page("unread")] == ["m2", "m1"]
    assert len(store.list_page("all")) == 3
    assert [m["id"] for m in store.list_page("all", limit=1, offset=1)] == ["m2"]


def test_search_matches_body_and_sender_and_survives_syntax(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, body="quarterly budget forecast"),
                  msg(2, sender="Priya Nair <p@x.com>")])
    assert [m["id"] for m in store.search("budget")] == ["m1"]
    assert [m["id"] for m in store.search("priya")] == ["m2"]
    assert store.search('AND OR "unbalanced') == []             # no OperationalError


def test_update_labels_and_unread_and_counts(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1)])
    store.update_labels("m1", add=[], remove=["UNREAD", "INBOX"])   # archive + read
    got = store.get("m1")
    assert got["is_read"] is True and "INBOX" not in got["labels"]
    store.update_labels("m1", add=["UNREAD"], remove=[])
    assert store.get("m1")["is_read"] is False
    assert store.counts() == {"total": 1, "unread": 1}
    assert [m["id"] for m in store.unread()] == ["m1"]


def test_delete_and_prune_not_seen(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1), msg(2)])
    store.delete(["m1"])
    assert store.get("m1") is None
    store.upsert([msg(2, last_seen="run-b"), msg(3)])           # m3 has no last_seen
    n = store.prune_not_seen("run-b", since_iso="2026-01-01T00:00:00+00:00")
    assert n == 1 and store.get("m3") is None and store.get("m2") is not None


def test_sync_state_kv(tmp_path):
    store = make_store(tmp_path)
    assert store.get_state("gmail_history_id") is None
    store.set_state("gmail_history_id", "1234")
    assert store.get_state("gmail_history_id") == "1234"
    store.set_state("gmail_history_id", None)
    assert store.get_state("gmail_history_id") is None


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def raw_msg(parts=None, body_data=None, mime="text/plain", labels=("INBOX", "UNREAD")):
    payload = {"mimeType": mime, "headers": [
        {"name": "From", "value": "Ada <ada@x.com>"},
        {"name": "To", "value": "me@x.com"},
        {"name": "Subject", "value": "Engines"}]}
    if parts is not None:
        payload["mimeType"] = "multipart/mixed"
        payload["parts"] = parts
    elif body_data is not None:
        payload["body"] = {"data": b64(body_data)}
    return {"id": "m9", "threadId": "t9", "labelIds": list(labels),
            "snippet": "snip", "internalDate": "1783881600000", "payload": payload}


def test_normalize_plain_text():
    m = normalize_message(raw_msg(body_data="hello world"))
    assert m["id"] == "m9" and m["sender"] == "Ada <ada@x.com>"
    assert m["subject"] == "Engines" and m["body"] == "hello world"
    assert m["is_read"] is False and "UNREAD" in m["labels"]
    assert m["received_at"].startswith("2026-07-12T")   # 1783881600000 ms UTC


def test_normalize_multipart_prefers_plain_and_lists_attachments():
    parts = [
        {"mimeType": "text/html", "filename": "",
         "body": {"data": b64("<p>rich <b>text</b></p>")}},
        {"mimeType": "text/plain", "filename": "",
         "body": {"data": b64("plain wins")}},
        {"mimeType": "application/pdf", "filename": "report.pdf", "body": {}},
    ]
    m = normalize_message(raw_msg(parts=parts))
    assert m["body"] == "plain wins" and m["attachments"] == ["report.pdf"]


def test_normalize_html_only_strips_tags():
    parts = [{"mimeType": "text/html", "filename": "",
              "body": {"data": b64("<div>Hello&nbsp;<b>there</b></div>")}}]
    m = normalize_message(raw_msg(parts=parts))
    assert "Hello" in m["body"] and "there" in m["body"] and "<" not in m["body"]


def test_normalize_nested_parts_and_read_state():
    inner = {"mimeType": "multipart/alternative", "filename": "", "parts": [
        {"mimeType": "text/plain", "filename": "", "body": {"data": b64("deep")}}]}
    m = normalize_message(raw_msg(parts=[inner], labels=("INBOX",)))
    assert m["body"] == "deep" and m["is_read"] is True


# GmailSync tests (Task 5)

from lumen.daemon.config import GoogleConfig, SyncConfig
from lumen.daemon.connectors.email_menu import (
    CURSOR_KEY, HISTORY_KEY, LAST_SYNC_KEY, RUN_KEY, GmailSync)


class FakeExec:
    def __init__(self, result):
        self._r = result

    def execute(self):
        if isinstance(self._r, Exception):
            raise self._r
        return self._r


class FakeMessages:
    def __init__(self, pages, full):
        self._pages, self._full = pages, full   # pages: token -> response
        self.list_calls = []

    def list(self, userId, q=None, maxResults=None, pageToken=None,
             includeSpamTrash=False):
        self.list_calls.append(pageToken)
        return FakeExec(self._pages[pageToken])

    def get(self, userId, id, format):
        return FakeExec(self._full[id])


class FakeService:
    def __init__(self, pages, full, profile_history="h100", history_pages=None):
        self._messages = FakeMessages(pages, full)
        self._profile = {"historyId": profile_history}
        self._history_pages = history_pages or {}
        self.history_calls = []

    def users(self):
        return self

    def messages(self):
        return self._messages

    def getProfile(self, userId):
        return FakeExec(self._profile)

    def history(self):
        return self

    def list(self, userId, startHistoryId=None, pageToken=None, historyTypes=None):
        self.history_calls.append(startHistoryId)
        return FakeExec(self._history_pages[pageToken])


def full_for(*ids):
    return {i: raw_msg() | {"id": i, "threadId": f"t-{i}"} for i in ids}


def make_sync(tmp_path, service):
    store = make_store(tmp_path)
    sync = GmailSync(store, GoogleConfig(), SyncConfig(),
                     service_factory=lambda: service)
    return store, sync


async def test_bulk_pull_pages_and_stores_history_id(tmp_path):
    pages = {None: {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
             "p2": {"messages": [{"id": "c"}]}}
    store, sync = make_sync(tmp_path, FakeService(pages, full_for("a", "b", "c")))
    assert await sync.sync_once() is True
    assert store.counts()["total"] == 3
    assert store.get_state(HISTORY_KEY) == "h100"     # captured at bulk START
    assert store.get_state(CURSOR_KEY) is None        # cleared on completion
    assert store.get_state(LAST_SYNC_KEY) is not None


async def test_bulk_resumes_from_persisted_cursor(tmp_path):
    boom = RuntimeError("network died")

    class DyingMessages(FakeMessages):
        def get(self, userId, id, format):
            if id == "c":
                return FakeExec(boom)
            return super().get(userId, id, format)

    pages = {None: {"messages": [{"id": "a"}], "nextPageToken": "p2"},
             "p2": {"messages": [{"id": "c"}]}}
    svc = FakeService(pages, full_for("a", "c"))
    svc._messages = DyingMessages(pages, full_for("a", "c"))
    store, sync = make_sync(tmp_path, svc)
    assert await sync.sync_once() is False            # page 2 died
    assert store.get_state(CURSOR_KEY) == "p2"        # page 1 persisted
    assert store.counts()["total"] == 1

    svc2 = FakeService(pages, full_for("a", "c"))     # healthy service, same store
    sync2 = GmailSync(store, GoogleConfig(), SyncConfig(),
                      service_factory=lambda: svc2)
    assert await sync2.sync_once() is True
    assert svc2._messages.list_calls == ["p2"]        # resumed, not restarted
    assert store.counts()["total"] == 2


async def test_bulk_page_one_failure_still_resumes_as_bulk(tmp_path):
    boom = RuntimeError("network died")
    pages = {None: boom}
    svc = FakeService(pages, {})
    store, sync = make_sync(tmp_path, svc)
    assert await sync.sync_once() is False             # page 1 died
    assert store.get_state(CURSOR_KEY) == ""            # pending sentinel, not None
    assert store.get_state(HISTORY_KEY) is not None

    svc2 = FakeService({None: {"messages": [{"id": "a"}]}}, full_for("a"))
    sync2 = GmailSync(store, GoogleConfig(), SyncConfig(),
                      service_factory=lambda: svc2)
    assert await sync2.sync_once() is True
    assert store.counts()["total"] == 1
    assert store.get_state(CURSOR_KEY) is None
    assert svc2._messages.list_calls == [None]          # re-entered BULK at page 1
    assert svc2.history_calls == []                     # not the _incremental stub


async def test_not_connected_is_normal(tmp_path):
    store = make_store(tmp_path)
    sync = GmailSync(store, GoogleConfig(), SyncConfig(),
                     service_factory=lambda: None)
    assert await sync.sync_once() is False
    assert store.get_state(HISTORY_KEY) is None
