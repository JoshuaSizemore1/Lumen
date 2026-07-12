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
