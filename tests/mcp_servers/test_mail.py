"""mail MCP server: search_email/get_email over the local email mirror.
Tests the underscored impl functions directly against a real tmp-file DB
seeded via EmailStore — no fakes needed since the mirror is just SQLite."""

from lumen.daemon import db
from lumen.daemon.connectors.email_menu import EmailStore
from lumen.mcp_servers import mail


def make_conn(tmp_path):
    return db.connect(tmp_path / "e.db")


def msg(i, **over):
    base = {"id": f"m{i}", "thread_id": f"t{i}", "sender": f"Sender {i} <s{i}@x.com>",
            "recipients": "me@x.com", "subject": f"Subject {i}",
            "body": f"body text {i}", "snippet": f"snip {i}",
            "labels": ["INBOX", "UNREAD"], "received_at": f"2026-07-{i:02d}T10:00:00+00:00",
            "is_read": False, "attachments": []}
    base.update(over)
    return base


def test_search_email_hits_and_empty(tmp_path):
    conn = make_conn(tmp_path)
    EmailStore(conn).upsert([msg(1, subject="Budget forecast", sender="Ada <ada@x.com>")])
    out = mail._search_email(conn, "budget", 5)
    assert "m1" in out and "Budget forecast" in out
    # empty result is query-scoped, never "you have no email" (todo-fixes #11)
    miss = mail._search_email(conn, "zzzz", 5)
    assert "No email matched" in miss and "most recent" in miss


def test_search_email_empty_query_returns_recent(tmp_path):
    # 'most recent email' → the model calls with an empty query (todo-fixes #13)
    conn = make_conn(tmp_path)
    EmailStore(conn).upsert([msg(1), msg(9), msg(4)])
    out = mail._search_email(conn, "", 5)
    assert out.index("id=m9") < out.index("id=m4") < out.index("id=m1")   # newest first


def test_search_email_renders_local_date():
    # UTC 07-09T02:00 shows as the user's local date, not the UTC calendar day
    import os
    import time
    prev = os.environ.get("TZ")
    os.environ["TZ"] = "America/Chicago"          # CDT = UTC-05:00 in July
    time.tzset()
    try:
        assert mail._local_dt("2026-07-09T02:00:00+00:00").startswith("2026-07-08")
        assert mail._local_dt("garbage") == "garbage"     # bad input → graceful
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        time.tzset()


def test_get_email_renders_from_to_date_subject_and_body(tmp_path):
    conn = make_conn(tmp_path)
    EmailStore(conn).upsert([msg(1, subject="Budget forecast", sender="Ada <ada@x.com>",
                                 recipients="me@x.com", body="Here's the forecast.")])
    out = mail._get_email(conn, "m1")
    assert "From: Ada <ada@x.com>" in out
    assert "To: me@x.com" in out
    assert "Date: 2026-07-01T10:00:00+00:00" in out
    assert "Subject: Budget forecast" in out
    assert "Here's the forecast." in out


def test_get_email_miss(tmp_path):
    conn = make_conn(tmp_path)
    EmailStore(conn).upsert([msg(1)])
    assert mail._get_email(conn, "zzz") == "No email with that id."


def test_get_email_truncates_long_body(tmp_path):
    conn = make_conn(tmp_path)
    long_body = "x" * 5000
    EmailStore(conn).upsert([msg(2, body=long_body)])
    out = mail._get_email(conn, "m2")
    assert len(out) < 4000
    assert "truncated" in out


def test_get_email_shows_attachments_when_present(tmp_path):
    conn = make_conn(tmp_path)
    EmailStore(conn).upsert([msg(3, attachments=["report.pdf", "notes.txt"])])
    out = mail._get_email(conn, "m3")
    assert "Attachments: report.pdf, notes.txt" in out


def test_get_email_omits_attachments_line_when_absent(tmp_path):
    conn = make_conn(tmp_path)
    EmailStore(conn).upsert([msg(4)])
    out = mail._get_email(conn, "m4")
    assert "Attachments:" not in out
