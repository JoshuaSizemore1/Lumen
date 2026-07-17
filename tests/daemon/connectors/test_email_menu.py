"""EmailStore: mirror reads/writes over the Task-1 schema."""
import asyncio
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
    assert [m["id"] for m in store.list_page("unread")] == ["m1"]  # inbox-scoped
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
    assert store.unread() == []    # unread but no INBOX — left the inbox


def test_labels_map_and_user_labels(tmp_path):
    store = make_store(tmp_path)
    assert store.labels_map() == {} and store.user_labels() == []
    store.set_labels([{"id": "INBOX", "name": "INBOX", "type": "system"},
                      {"id": "Label_7", "name": "Bills", "type": "user"},
                      {"id": "Label_9", "name": "BSA", "type": "user"}])
    assert store.labels_map()["Label_7"] == "Bills"
    assert [l["name"] for l in store.user_labels()] == ["Bills", "BSA"]
    assert store.label_id("Bills") == "Label_7"
    assert store.label_id("bills") == "Label_7"          # casefold fallback
    assert store.label_id("INBOX") is None               # system labels hidden
    store.set_labels([{"id": "Label_9", "name": "BSA", "type": "user"}])
    assert store.label_id("Bills") is None               # replace-all
    store.upsert_label("Label_7", "Bills")
    assert store.label_id("Bills") == "Label_7"


def test_list_page_label_scope_and_present_ids(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, labels=["INBOX", "UNREAD"]),
                  msg(2, labels=["Label_7"]),
                  msg(3, labels=["INBOX", "Label_7"])])
    assert [m["id"] for m in store.list_page("label", label_id="Label_7")] \
        == ["m3", "m2"]
    assert store.present_label_ids() == {"INBOX", "UNREAD", "Label_7"}


def test_inbox_excludes_user_labeled_mail(tmp_path):
    # Design 2026-07-16: each label chip is its own inbox — the default
    # "inbox" view is INBOX mail carrying no user label, so mail labeled in
    # Gmail without being archived still leaves the default view.
    store = make_store(tmp_path)
    store.set_labels([{"id": "Label_7", "name": "Bills", "type": "user"},
                      {"id": "IMPORTANT", "name": "IMPORTANT", "type": "system"}])
    store.upsert([msg(1),                                       # plain inbox
                  msg(2, labels=["INBOX", "UNREAD", "Label_7"]),  # labeled upstream
                  msg(3, labels=["INBOX", "UNREAD", "IMPORTANT"])])
    assert [m["id"] for m in store.list_page("inbox")] == ["m3", "m1"]
    assert [m["id"] for m in store.list_page("unread")] == ["m3", "m1"]
    assert [m["id"] for m in store.list_page("label", label_id="Label_7")] == ["m2"]


def test_unread_filter_is_inbox_scoped(tmp_path):
    # Design 2026-07-15: labeled mail has left the inbox — "unread" means
    # INBOX + UNREAD, so rule-filed newsletters stop nagging chat/briefing.
    store = make_store(tmp_path)
    store.upsert([msg(1), msg(2, labels=["UNREAD"])])    # m2 unread, archived
    assert [m["id"] for m in store.list_page("unread")] == ["m1"]
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


def test_normalize_keeps_raw_html():
    parts = [
        {"mimeType": "text/html", "filename": "",
         "body": {"data": b64("<p>rich <b>text</b></p>")}},
        {"mimeType": "text/plain", "filename": "",
         "body": {"data": b64("plain wins")}},
    ]
    m = normalize_message(raw_msg(parts=parts))
    assert m["body"] == "plain wins"
    assert m["body_html"] == "<p>rich <b>text</b></p>"
    # '' (not NULL) when the message simply has no HTML part
    assert normalize_message(raw_msg(body_data="just text"))["body_html"] == ""


def test_body_html_stored_but_kept_out_of_lists(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, body_html="<p>hi</p>")])
    assert store.get("m1")["body_html"] == "<p>hi</p>"
    assert "body_html" not in store.list_page("inbox")[0]      # lists stay light
    assert "body_html" not in store.search("subject")[0]
    store.set_body_html("m1", "<div>new</div>")
    assert store.get("m1")["body_html"] == "<div>new</div>"


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
        self.modify_calls = []

    def list(self, userId, q=None, maxResults=None, pageToken=None,
             includeSpamTrash=False):
        self.list_calls.append(pageToken)
        return FakeExec(self._pages[pageToken])

    def get(self, userId, id, format):
        return FakeExec(self._full[id])

    def modify(self, userId, id, body):
        self.modify_calls.append((id, body))
        return FakeExec({})


class FakeLabelsApi:
    def __init__(self, labels, created_id="Label_9"):
        self._labels, self._created_id = labels, created_id
        self.created = []

    def list(self, userId):
        return FakeExec({"labels": self._labels})

    def create(self, userId, body):
        self.created.append(body["name"])
        return FakeExec({"id": self._created_id, "name": body["name"]})


class FakeService:
    def __init__(self, pages, full, profile_history="h100", history_pages=None,
                 labels=None):
        self._messages = FakeMessages(pages, full)
        self._labels_api = FakeLabelsApi(labels or [])
        self._profile = {"historyId": profile_history}
        self._history_pages = history_pages or {}
        self.history_calls = []

    def users(self):
        return self

    def messages(self):
        return self._messages

    def labels(self):
        return self._labels_api

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


# Label plumbing + rules in the sync path (2026-07-15)

from lumen.daemon.connectors.email_menu import EmailStore
from lumen.daemon.connectors.mail_rules import RuleStore


async def test_refresh_labels_and_apply_label(tmp_path):
    svc = FakeService({}, {}, labels=[
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "Label_7", "name": "Bills", "type": "user"}])
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])

    assert await sync.refresh_labels() is True
    assert store.label_id("Bills") == "Label_7"

    assert await sync.apply_label("m1", "Bills") is True
    assert svc._messages.modify_calls == [("m1", {"addLabelIds": ["Label_7"],
                                                  "removeLabelIds": ["INBOX"]})]
    got = store.get("m1")
    assert "Label_7" in got["labels"] and "INBOX" not in got["labels"]

    # unknown label -> created in Gmail first, then applied
    assert await sync.apply_label("m1", "BSA") is True
    assert svc._labels_api.created == ["BSA"]
    assert store.label_id("BSA") == "Label_9"


async def test_incremental_applies_rules_to_new_inbox_mail(tmp_path):
    # A new INBOX message arriving via the History delta gets the matching
    # rule's label and loses INBOX; SENT mail is untouched.
    history = {None: {"history": [
        {"messagesAdded": [{"message": {"id": "billmail"}},
                           {"message": {"id": "sentmail"}}]},
    ], "historyId": "h200"}}
    full = {"billmail": raw_msg() | {"id": "billmail", "threadId": "t1"},
            "sentmail": raw_msg(labels=("SENT", "INBOX"))
                        | {"id": "sentmail", "threadId": "t2"}}
    svc = FakeService({}, full, history_pages=history,
                      labels=[{"id": "Label_7", "name": "Bills", "type": "user"}])
    conn = db.connect(tmp_path / "e.db")
    store, rules = EmailStore(conn), RuleStore(conn)
    rules.add({"label": "Bills", "from_addrs": [], "domains": ["x.com"],
               "subject_kw": [], "body_kw": []})
    sync = GmailSync(store, GoogleConfig(), SyncConfig(),
                     service_factory=lambda: svc, rules=rules)
    store.set_state(HISTORY_KEY, "h100")

    assert await sync.sync_once() is True
    assert svc._messages.modify_calls == [
        ("billmail", {"addLabelIds": ["Label_7"], "removeLabelIds": ["INBOX"]})]
    got = store.get("billmail")
    assert "Label_7" in got["labels"] and "INBOX" not in got["labels"]
    assert "INBOX" in store.get("sentmail")["labels"]


# Task 6: Incremental sync
async def test_incremental_applies_adds_deletes_and_label_flips(tmp_path):
    history = {None: {"history": [
        {"messagesAdded": [{"message": {"id": "new1"}}]},
        {"messagesDeleted": [{"message": {"id": "old1"}}]},
        {"labelsRemoved": [{"message": {"id": "keep1"}, "labelIds": ["UNREAD"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({}, full_for("new1"), history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1) | {"id": "old1"}, msg(2) | {"id": "keep1"}])
    store.set_state(HISTORY_KEY, "h100")
    assert await sync.sync_once() is True
    assert svc.history_calls == ["h100"]
    assert store.get("new1") is not None
    assert store.get("old1") is None
    assert store.get("keep1")["is_read"] is True
    assert store.get_state(HISTORY_KEY) == "h200"


async def test_expired_history_rebaselines_and_prunes(tmp_path):
    class Expired(Exception):
        status_code = 404

    pages = {None: {"messages": [{"id": "fresh"}]}}
    svc = FakeService(pages, full_for("fresh"),
                      profile_history="h300",
                      history_pages={None: Expired()})
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1) | {"id": "gap-deleted"}])    # in window, gone upstream
    store.set_state(HISTORY_KEY, "h-expired")
    assert await sync.sync_once() is True
    assert store.get("fresh") is not None
    assert store.get("gap-deleted") is None           # pruned by run-id pass
    assert store.get_state(HISTORY_KEY) == "h300"
    assert store.get_state(RUN_KEY) is None


class NotFoundError(Exception):
    status_code = 404


class Maybe404Messages(FakeMessages):
    """get() raises a 404-shaped error for any id absent from `full` — mirrors
    Gmail 404ing gets of purged messages."""
    def get(self, userId, id, format):
        if id not in self._full:
            return FakeExec(NotFoundError(f"no such message: {id}"))
        return super().get(userId, id, format)


async def test_incremental_same_page_add_then_delete_no_zombie(tmp_path):
    # "ghost" is added then permanently deleted within the same history page —
    # the batch add-fetch must not resurrect it after the delete.
    history = {None: {"history": [
        {"messagesAdded": [{"message": {"id": "ghost"}}]},
        {"messagesDeleted": [{"message": {"id": "ghost"}}]},
    ], "historyId": "h200"}}
    svc = FakeService({}, full_for(), history_pages=history)
    svc._messages = Maybe404Messages({}, full_for())
    store, sync = make_sync(tmp_path, svc)
    store.set_state(HISTORY_KEY, "h100")
    assert await sync.sync_once() is True
    assert store.get("ghost") is None
    assert store.get_state(HISTORY_KEY) == "h200"


async def test_incremental_add_fetch_failure_retries_next_poll(tmp_path):
    # A non-404 failure fetching an added id must not advance HISTORY_KEY —
    # the next poll should retry the identical window.
    boom = RuntimeError("network died")

    class DyingGetMessages(FakeMessages):
        def get(self, userId, id, format):
            if id == "x":
                return FakeExec(boom)
            return super().get(userId, id, format)

    history = {None: {"history": [
        {"messagesAdded": [{"message": {"id": "x"}}]},
    ], "historyId": "h200"}}
    svc = FakeService({}, full_for(), history_pages=history)
    svc._messages = DyingGetMessages({}, full_for())
    store, sync = make_sync(tmp_path, svc)
    store.set_state(HISTORY_KEY, "h100")
    assert await sync.sync_once() is False
    assert store.get_state(HISTORY_KEY) == "h100"


# Task 7: Manage-action executors (archive / mark-read)

class ModifyingService(FakeService):
    def __init__(self, *a, fail=False, **kw):
        super().__init__(*a, **kw)
        self.modified, self._fail = [], fail

    def modify(self, userId, id, body):
        if self._fail:
            return FakeExec(RuntimeError("api down"))
        self.modified.append((id, body))
        return FakeExec({"id": id})


async def test_archive_hits_api_then_mirror(tmp_path):
    svc = ModifyingService({}, {})
    svc._messages.modify = svc.modify           # messages() facade carries modify
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    assert await sync.archive("m1") is True
    assert svc.modified == [("m1", {"removeLabelIds": ["INBOX"]})]
    assert "INBOX" not in store.get("m1")["labels"]


async def test_mark_read_and_unread(tmp_path):
    svc = ModifyingService({}, {})
    svc._messages.modify = svc.modify
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    assert await sync.mark_read("m1", True) is True
    assert store.get("m1")["is_read"] is True
    assert await sync.mark_read("m1", False) is True
    assert store.get("m1")["is_read"] is False
    assert svc.modified == [("m1", {"removeLabelIds": ["UNREAD"]}),
                            ("m1", {"addLabelIds": ["UNREAD"]})]


async def test_action_failure_leaves_mirror_untouched(tmp_path):
    svc = ModifyingService({}, {}, fail=True)
    svc._messages.modify = svc.modify
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    assert await sync.archive("m1") is False
    assert "INBOX" in store.get("m1")["labels"]


# Final-review fix: sync_once must be serialized so the poller and mail.refresh
# can never run overlapping bulk pulls against the same persisted cursor.
async def test_sync_once_serialized(tmp_path):
    store = make_store(tmp_path)
    store.set_state(HISTORY_KEY, None)   # both calls route through _bulk
    sync = GmailSync(store, GoogleConfig(), SyncConfig(),
                     service_factory=lambda: object())
    order = []

    async def fake_bulk(service):
        order.append("enter")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        order.append("exit")
        return True

    sync._bulk = fake_bulk
    await asyncio.gather(sync.sync_once(), sync.sync_once())
    assert order == ["enter", "exit", "enter", "exit"]


# ---- send (Phase 7) --------------------------------------------------------

class SendService:
    """users().messages().send/get fake capturing the outgoing payload."""

    def __init__(self, message_id_header="<orig@mail.gmail.com>", fail=False):
        self.sent, self._hdr, self._fail = [], message_id_header, fail

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, format, metadataHeaders=None):
        return FakeExec({"payload": {"headers": [
            {"name": "Message-ID", "value": self._hdr}]}})

    def send(self, userId, body):
        if self._fail:
            return FakeExec(RuntimeError("boom"))
        self.sent.append(body)
        return FakeExec({"id": "sent1"})


def sent_mime(svc):
    import email
    return email.message_from_bytes(
        base64.urlsafe_b64decode(svc.sent[0]["raw"]))


async def test_send_builds_rfc822_and_posts(tmp_path):
    svc = SendService()
    _store, sync = make_sync(tmp_path, svc)
    ok = await sync.send(["a@x.com"], ["c@x.com"], ["b@x.com"], "Subj", "Body text")
    assert ok is True
    m = sent_mime(svc)
    assert m["To"] == "a@x.com" and m["Cc"] == "c@x.com" and m["Bcc"] == "b@x.com"
    assert m["Subject"] == "Subj" and "Body text" in m.get_payload()
    assert "threadId" not in svc.sent[0]


async def test_send_reply_threads_via_mirror_and_message_id(tmp_path):
    svc = SendService()
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])                     # id m1, thread_id t1
    ok = await sync.send(["s1@x.com"], [], [], "Re: Subject 1", "b", reply_to="m1")
    assert ok is True
    assert svc.sent[0]["threadId"] == "t1"
    m = sent_mime(svc)
    assert m["In-Reply-To"] == "<orig@mail.gmail.com>"
    assert m["References"] == "<orig@mail.gmail.com>"


async def test_send_api_failure_returns_false(tmp_path):
    _store, sync = make_sync(tmp_path, SendService(fail=True))
    assert await sync.send(["a@x.com"], [], [], "s", "b") is False


def test_sent_query_walks_forward_from_cursor(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, labels=["SENT"]), msg(2, labels=["SENT"]),
                  msg(3, labels=["INBOX"]),                    # not sent
                  msg(4, labels=["SENT", "INBOX"])])           # self-send counts
    got = store.sent("2026-07-01T10:00:00+00:00", limit=10)
    assert [m["id"] for m in got] == ["m2", "m4"]              # oldest first, after cursor
    assert store.sent("2026-07-01T10:00:00+00:00", limit=1)[0]["id"] == "m2"


def test_involving_matches_either_direction_newest_first(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, sender="Ada <ada@x.com>", recipients="me@x.com"),
                  msg(2, sender="Me <me@x.com>", recipients="Ada <ada@x.com>"),
                  msg(3, sender="Bob <bob@y.com>", recipients="me@x.com")])
    got = store.involving("ada@x.com")
    assert [m["id"] for m in got] == ["m2", "m1"]
    assert store.involving("ada@x.com", limit=1) == [got[0]]
    assert store.involving("nobody@z.com") == []


def test_involving_escapes_like_wildcards(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, sender="A <a_b@x.com>"), msg(2, sender="B <axb@x.com>")])
    assert [m["id"] for m in store.involving("a_b@x.com")] == ["m1"]


async def test_fetch_html_backfills_legacy_rows(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1)])                       # pre-column row: NULL body_html
    assert store.get("m1")["body_html"] is None
    full = {"m1": raw_msg(parts=[
        {"mimeType": "text/html", "filename": "",
         "body": {"data": b64("<i>hi</i>")}}]) | {"id": "m1"}}
    _store, sync = make_sync(tmp_path, FakeService({}, full))
    sync._store = store
    assert await sync.fetch_html("m1") == "<i>hi</i>"
    assert store.get("m1")["body_html"] == "<i>hi</i>"


async def test_fetch_html_failure_leaves_null(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1)])

    class Dead:
        def users(self):
            raise RuntimeError("offline")

    sync = GmailSync(store, GoogleConfig(), SyncConfig(),
                     service_factory=lambda: Dead())
    assert await sync.fetch_html("m1") is None
    assert store.get("m1")["body_html"] is None   # retried on a later open
