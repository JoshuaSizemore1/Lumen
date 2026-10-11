"""Regression tests for #104: Gmail-side trash, spam, and drafts.

Each test uses the real EmailStore / GmailSync on a temp DB (not a fake store)
so that the SQL filters, FTS triggers, and sync logic are all exercised together.
"""
import asyncio

import pytest

from lumen.daemon import db
from lumen.daemon.config import GoogleConfig, SyncConfig
from lumen.daemon.connectors.email_menu import (
    HISTORY_KEY,
    EmailStore,
    GmailSync,
    normalize_message,
)

# ---- shared helpers ----------------------------------------------------------


def make_store(tmp_path):
    return EmailStore(db.connect(tmp_path / "e.db"))


def msg(i, labels=("INBOX", "UNREAD"), **over):
    base = {
        "id": f"m{i}",
        "thread_id": f"t{i}",
        "sender": f"Sender {i} <s{i}@x.com>",
        "recipients": "me@x.com",
        "subject": f"Subject {i}",
        "body": f"body text {i}",
        "body_html": None,
        "snippet": f"snip {i}",
        "labels": list(labels),
        "received_at": f"2026-07-{i:02d}T10:00:00+00:00",
        "is_read": "UNREAD" not in labels,
        "attachments": [],
    }
    base.update(over)
    return base


import base64


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def raw_msg_with_labels(mid: str, labels: tuple) -> dict:
    """A minimal Gmail API messages.get response with the given labels."""
    return {
        "id": mid,
        "threadId": f"t-{mid}",
        "labelIds": list(labels),
        "snippet": "snip",
        "internalDate": "1783881600000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Ada <ada@x.com>"},
                {"name": "To", "value": "me@x.com"},
                {"name": "Subject", "value": "Test"},
            ],
            "body": {"data": _b64("hello")},
        },
    }


class FakeExec:
    def __init__(self, result):
        self._r = result

    def execute(self):
        if isinstance(self._r, Exception):
            raise self._r
        return self._r


class FakeMessages:
    def __init__(self, full: dict):
        self._full = full
        self.trash_calls = []
        self.modify_calls = []

    def list(self, userId, q=None, maxResults=None, pageToken=None,
             includeSpamTrash=False):
        return FakeExec({"messages": []})

    def get(self, userId, id, format):
        if id not in self._full:
            return FakeExec(KeyError(f"no message: {id}"))
        return FakeExec(self._full[id])

    def modify(self, userId, id, body):
        self.modify_calls.append((id, body))
        return FakeExec({})

    def trash(self, userId, id):
        self.trash_calls.append(id)
        return FakeExec({})


class FakeHistory:
    def __init__(self, pages: dict):
        self._pages = pages

    def list(self, userId, startHistoryId=None, pageToken=None, historyTypes=None):
        return FakeExec(self._pages[pageToken])


class FakeBulkMessages(FakeMessages):
    """Supports `list` returning real pages for the bulk-pull tests."""

    def __init__(self, pages: dict, full: dict):
        super().__init__(full)
        self._pages = pages

    def list(self, userId, q=None, maxResults=None, pageToken=None,
             includeSpamTrash=False):
        return FakeExec(self._pages.get(pageToken, {"messages": []}))


class FakeService:
    def __init__(self, full: dict, history_pages: dict | None = None,
                 bulk_pages: dict | None = None):
        full_msgs = FakeBulkMessages(bulk_pages or {}, full) \
            if bulk_pages else FakeMessages(full)
        self._messages = full_msgs
        self._history = FakeHistory(history_pages or {None: {"historyId": "h999",
                                                             "history": []}})
        self._profile = {"historyId": "h100"}
        self._labels_resp = {"labels": []}

    def users(self):
        return self

    def messages(self):
        return self._messages

    def history(self):
        return self._history

    def labels(self):
        return self

    def list(self, userId, startHistoryId=None, pageToken=None, historyTypes=None):
        return self._history.list(userId, startHistoryId, pageToken, historyTypes)

    def getProfile(self, userId):
        return FakeExec(self._profile)


def make_sync(tmp_path, service):
    store = make_store(tmp_path)
    sync = GmailSync(store, GoogleConfig(), SyncConfig(),
                     service_factory=lambda: service)
    return store, sync


# ---- query-level filtering (legacy / defence-in-depth) ----------------------


def test_list_page_excludes_trash(tmp_path):
    """A row with TRASH label must never appear in list_page results."""
    store = make_store(tmp_path)
    store.upsert([msg(1),
                  msg(2, labels=["INBOX", "TRASH"])])
    # inbox, unread, all — none should show the trashed row
    assert all(m["id"] != "m2" for m in store.list_page("inbox"))
    assert all(m["id"] != "m2" for m in store.list_page("all"))


def test_list_page_excludes_spam(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1), msg(2, labels=["SPAM"])])
    assert all(m["id"] != "m2" for m in store.list_page("all"))


def test_list_page_excludes_draft(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1), msg(2, labels=["DRAFT"])])
    assert all(m["id"] != "m2" for m in store.list_page("all"))


def test_search_excludes_trash_spam_draft(tmp_path):
    store = make_store(tmp_path)
    store.upsert([
        msg(1),                                     # normal inbox mail
        msg(2, labels=["TRASH"], subject="trash it"),
        msg(3, labels=["SPAM"], subject="spam it"),
        msg(4, labels=["DRAFT"], subject="draft it"),
    ])
    # Direct subject search that would match each hidden message
    assert store.search("trash it") == []
    assert store.search("spam it") == []
    assert store.search("draft it") == []
    # Empty query returns only the visible message
    assert [m["id"] for m in store.search("")] == ["m1"]


def test_involving_excludes_trash_spam_draft(tmp_path):
    store = make_store(tmp_path)
    store.upsert([
        msg(1, sender="Ada <ada@x.com>"),
        msg(2, sender="Ada <ada@x.com>", labels=["TRASH"]),
        msg(3, sender="Ada <ada@x.com>", labels=["SPAM"]),
        msg(4, sender="Ada <ada@x.com>", labels=["DRAFT"]),
    ])
    got = store.involving("ada@x.com")
    assert [m["id"] for m in got] == ["m1"]


def test_sent_excludes_trash(tmp_path):
    """A sent message that was later trashed shouldn't appear in the sent view."""
    store = make_store(tmp_path)
    store.upsert([
        msg(1, labels=["SENT"]),
        msg(2, labels=["SENT", "TRASH"]),
    ])
    got = store.sent("2026-07-01T00:00:00+00:00")
    assert [m["id"] for m in got] == ["m1"]


# ---- migration: existing hidden-label rows are cleaned up at startup ---------


def test_db_migration_removes_existing_trash_spam_draft(tmp_path):
    """Rows that were stored before #104 (TRASH/SPAM/DRAFT labels) are deleted
    the next time db.connect runs (the migration step)."""
    conn = db.connect(tmp_path / "e.db")
    store = EmailStore(conn)
    store.upsert([
        msg(1),                              # normal — must survive
        msg(2, labels=["TRASH"]),
        msg(3, labels=["SPAM"]),
        msg(4, labels=["DRAFT"]),
    ])
    conn.close()
    # Re-open — migration runs again and cleans the hidden rows.
    conn2 = db.connect(tmp_path / "e.db")
    store2 = EmailStore(conn2)
    assert store2.get("m1") is not None
    assert store2.get("m2") is None
    assert store2.get("m3") is None
    assert store2.get("m4") is None


# ---- bulk pull: draft messages are filtered out ------------------------------


async def test_bulk_pull_filters_draft(tmp_path):
    """A DRAFT message in the bulk listing is fetched but not stored (#104)."""
    full = {
        "inbox1": raw_msg_with_labels("inbox1", ("INBOX",)),
        "draft1": raw_msg_with_labels("draft1", ("DRAFT",)),
    }
    bulk_pages = {
        None: {"messages": [{"id": "inbox1"}, {"id": "draft1"}]},
    }
    svc = FakeService(full, bulk_pages=bulk_pages)
    store, sync = make_sync(tmp_path, svc)
    assert await sync.sync_once() is True
    assert store.get("inbox1") is not None
    assert store.get("draft1") is None          # draft must not be stored


async def test_bulk_pull_filters_spam(tmp_path):
    """A SPAM-labelled message in the bulk listing is not stored.

    Note: in production, includeSpamTrash=False prevents SPAM from appearing
    in the listing; this tests the post-fetch filter as the second line of
    defence."""
    full = {
        "inbox1": raw_msg_with_labels("inbox1", ("INBOX",)),
        "spam1": raw_msg_with_labels("spam1", ("SPAM",)),
    }
    bulk_pages = {None: {"messages": [{"id": "inbox1"}, {"id": "spam1"}]}}
    svc = FakeService(full, bulk_pages=bulk_pages)
    store, sync = make_sync(tmp_path, svc)
    assert await sync.sync_once() is True
    assert store.get("inbox1") is not None
    assert store.get("spam1") is None


# ---- incremental: trash/spam label added → row deleted -----------------------


async def test_incremental_trash_label_added_deletes_row(tmp_path):
    """When Gmail adds TRASH to an existing message, the mirror row is deleted."""
    history = {None: {"history": [
        {"labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["TRASH"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({}, history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])                      # m1 in the mirror
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert store.get("m1") is None              # deleted from mirror
    assert store.get_state(HISTORY_KEY) == "h200"


async def test_incremental_spam_label_added_deletes_row(tmp_path):
    """When Gmail moves a message to Spam, the mirror row is deleted."""
    history = {None: {"history": [
        {"labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["SPAM"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({}, history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert store.get("m1") is None


async def test_incremental_draft_label_added_deletes_row(tmp_path):
    """Unlikely but handled: DRAFT label appearing on a mirrored row → delete."""
    history = {None: {"history": [
        {"labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["DRAFT"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({}, history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert store.get("m1") is None


# ---- incremental: spam/draft arriving via messagesAdded is not stored --------


async def test_incremental_new_spam_not_stored(tmp_path):
    """A new spam message arriving via messagesAdded must not enter the mirror."""
    spam_raw = raw_msg_with_labels("spam1", ("SPAM",))
    history = {None: {"history": [
        {"messagesAdded": [{"message": {"id": "spam1"}}]},
    ], "historyId": "h200"}}
    svc = FakeService({"spam1": spam_raw}, history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert store.get("spam1") is None


async def test_incremental_new_draft_not_stored(tmp_path):
    """A new draft arriving via messagesAdded must not enter the mirror."""
    draft_raw = raw_msg_with_labels("draft1", ("DRAFT",))
    history = {None: {"history": [
        {"messagesAdded": [{"message": {"id": "draft1"}}]},
    ], "historyId": "h200"}}
    svc = FakeService({"draft1": draft_raw}, history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert store.get("draft1") is None


# ---- incremental: message restored from Gmail Trash reappears ----------------


async def test_incremental_trash_removed_restores_row(tmp_path):
    """When TRASH label is removed from a message that is absent from the
    mirror (because it was deleted when it was trashed), the sync re-fetches
    it and inserts it back (#104 restore path)."""
    inbox_raw = raw_msg_with_labels("m1", ("INBOX",))
    history = {None: {"history": [
        {"labelsRemoved": [{"message": {"id": "m1"}, "labelIds": ["TRASH"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({"m1": inbox_raw}, history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    # m1 is NOT in the mirror (it was deleted when it was trashed)
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert store.get("m1") is not None          # restored from Trash
    assert "INBOX" in store.get("m1")["labels"]


async def test_incremental_trash_removed_but_still_trash_not_restored(tmp_path):
    """If TRASH is nominally removed but the fetched message still has TRASH
    (e.g. a partial label event), it must not be added to the mirror."""
    still_trash_raw = raw_msg_with_labels("m1", ("TRASH",))
    history = {None: {"history": [
        {"labelsRemoved": [{"message": {"id": "m1"}, "labelIds": ["TRASH"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({"m1": still_trash_raw}, history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert store.get("m1") is None


async def test_trash_and_undo_in_one_poll_window_keeps_the_mail(tmp_path):
    """Gmail's Undo seconds after a trash: both land in one 5-minute window.
    The trash used to bar the message for the whole run, so it was lost."""
    history = {None: {"history": [
        {"labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["TRASH"]}]},
        {"labelsRemoved": [{"message": {"id": "m1"}, "labelIds": ["TRASH"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({"m1": raw_msg_with_labels("m1", ("INBOX",))},
                      history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1, labels=["INBOX"])])
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert store.get("m1") is not None


async def test_not_spam_brings_the_mail_back(tmp_path):
    history = {None: {"history": [
        {"labelsRemoved": [{"message": {"id": "m1"}, "labelIds": ["SPAM"]}]},
        {"labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["INBOX"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({"m1": raw_msg_with_labels("m1", ("INBOX",))},
                      history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.set_state(HISTORY_KEY, "h100")       # m1 was dropped as spam

    assert await sync.sync_once() is True
    assert store.get("m1") is not None


async def test_a_failed_restore_fetch_retries_the_window(tmp_path):
    """A transient error fetching a restored message must not advance the
    history cursor, or the restore is never seen again."""
    history = {None: {"history": [
        {"labelsRemoved": [{"message": {"id": "m1"}, "labelIds": ["TRASH"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({}, history_pages=history)   # get() raises: not a 404
    store, sync = make_sync(tmp_path, svc)
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is False
    assert store.get_state(HISTORY_KEY) == "h100"


async def test_incremental_trash_removed_then_retrashed_same_delta(tmp_path):
    """If a message is untrashed and then re-trashed within the same delta,
    it must not end up in the mirror."""
    inbox_raw = raw_msg_with_labels("m1", ("INBOX",))
    history = {None: {"history": [
        # Untrash first, then trash again in the same batch.
        {"labelsRemoved": [{"message": {"id": "m1"}, "labelIds": ["TRASH"]}]},
        {"labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["TRASH"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({"m1": inbox_raw}, history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert store.get("m1") is None   # re-trashed within same delta — stays out


async def test_incremental_trash_removed_row_exists_update_labels_only(tmp_path):
    """If TRASH is removed from a message that IS already in the mirror
    (unusual but possible), update_labels is called and no extra fetch happens.
    The row should stay in the mirror with TRASH removed from its labels."""
    svc_full = {"m1": raw_msg_with_labels("m1", ("INBOX",))}
    history = {None: {"history": [
        {"labelsRemoved": [{"message": {"id": "m1"},
                            "labelIds": ["TRASH", "INBOX"]}]},
    ], "historyId": "h200"}}
    svc = FakeService(svc_full, history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    # m1 IS in the mirror with TRASH label (edge case)
    store.upsert([msg(1, labels=["INBOX", "TRASH"])])
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    got = store.get("m1")
    assert got is not None
    assert "TRASH" not in got["labels"]
