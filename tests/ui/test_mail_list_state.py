"""Mail list state (ui_v2/state.py, shared by v3/v4): the inbox's unread tally
follows the inbox, not the list on screen (#103); a failed load of another
mailbox doesn't leave the old one's mail under the new title (#108); and a
reload after star / label / archive keeps the reader, the search and the
selection where they were (#109)."""
import pytest

from lumen.ui_v3.state import AppState
from tests.ui.v4_support import FakeClient, _stub_canvas_creds  # noqa: F401


def row(i, unread=False, labels=("INBOX",)):
    labels = list(labels) + (["UNREAD"] if unread else [])
    return {"id": f"m{i}", "sender": f"S{i} <s{i}@x.com>", "subject": f"Sub {i}",
            "snippet": "", "body": f"body {i}", "labels": labels,
            "received_at": f"2026-10-0{i}T10:00:00+00:00", "is_read": not unread}


def summary(total, *rows):
    return {"total": total, "newest": list(rows)}


@pytest.fixture
def st(qtbot):
    data = FakeClient()
    s = AppState(data=data)
    return s, data


def answer(data, type_, result):
    _p, cb = data.last(type_)
    cb(result)


# ---- #103 ------------------------------------------------------------------

def test_unread_tally_follows_the_inbox_not_the_list(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1, True), row(2)],
                                  "inbox_unread": summary(7, row(1, True))})
    assert s.unread_count() == 7
    s.set_mail_scope("sent")
    answer(data, "emails.list", {"emails": [row(3, labels=("SENT",))],
                                 "inbox_unread": summary(7, row(1, True))})
    assert s.unread_count() == 7                    # not "Inbox clear"
    assert [m["id"] for m in s.unread_mails()] == ["m1"]
    s.search_mails("invoice")
    answer(data, "emails.search", {"emails": []})   # no tally: keep the last
    assert s.unread_count() == 7


def test_more_than_fifty_unread_is_counted(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1, True)] * 50,
                                  "inbox_unread": summary(120, row(1, True))})
    assert s.unread_count() == 120


def test_opening_an_inbox_message_takes_it_off_the_tally(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1, True), row(2, True)],
                                  "inbox_unread": summary(2, row(1, True),
                                                          row(2, True))})
    s.select_mail("m2")
    assert s.unread_count() == 1
    assert [m["id"] for m in s.unread_mails()] == ["m1"]


# ---- #108 ------------------------------------------------------------------

def test_a_failed_load_of_another_mailbox_clears_the_old_mail(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1), row(2)]})
    s.set_mail_scope("Receipts")
    assert data.fail("emails.list", msg="unknown label")
    assert s.mails == [] and s.selected_mail is None
    assert s.mail_error == "unknown label"


def test_a_failed_reload_of_the_same_mailbox_keeps_its_mail(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1), row(2)]})
    s.refresh_mails()
    assert data.fail("emails.list")
    assert [m["id"] for m in s.mails] == ["m1", "m2"]


# ---- #109 ------------------------------------------------------------------

def test_a_reload_keeps_the_fetched_body(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1), row(2)]})
    s.select_mail("m1")
    # select_mail also prefetches m2's body; answer m1's own request.
    cb = next(cb for t, p, cb in data.requests
              if t == "emails.get" and p == {"id": "m1"})
    cb({**row(1), "body_html": "<p>hi</p>"})
    assert s.sel_mail()["body_html"] == "<p>hi</p>"
    s.star_mail("m1", True)
    answer(data, "emails.star", {"ok": True, "message": "Starred."})
    answer(data, "emails.list", {"emails": [row(1, labels=("INBOX", "STARRED")),
                                            row(2)]})
    assert s.sel_mail()["body_html"] == "<p>hi</p>"   # no "Loading message…"
    assert s.sel_mail()["is_starred"]


def test_a_reload_during_a_search_reruns_the_search(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1), row(2)]})
    s.search_mails("engines")
    answer(data, "emails.search", {"emails": [row(2)]})
    s.star_mail("m2", True)
    answer(data, "emails.star", {"ok": True})
    payload, _cb = data.last("emails.search")
    assert data.request_types()[-1] == "emails.search"
    assert payload == {"query": "engines"}


def test_changing_mailbox_ends_the_search(st):
    s, data = st
    s.search_mails("engines")
    s.set_mail_scope("sent")
    assert s.mail_query == ""
    assert data.request_types()[-1] == "emails.list"


def test_after_archive_the_next_message_opens(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1), row(2), row(3)]})
    s.select_mail("m2")
    s.archive_mail("m2")
    answer(data, "emails.archive", {"ok": True, "message": "Archived."})
    answer(data, "emails.list", {"emails": [row(1), row(3)]})
    assert s.selected_mail == "m3"                  # not back to the top


def test_archiving_the_last_message_opens_the_one_above(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1), row(2), row(3)]})
    s.select_mail("m3")
    s.archive_mail("m3")
    answer(data, "emails.archive", {"ok": True})
    answer(data, "emails.list", {"emails": [row(1), row(2)]})
    assert s.selected_mail == "m2"


def test_a_new_mailbox_starts_at_the_top(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1), row(2), row(3)]})
    s.select_mail("m2")
    s.set_mail_scope("unread")
    answer(data, "emails.list", {"emails": [row(1, True), row(3, True)]})
    assert s.selected_mail == "m1"


# ---- opening a message from Today (follows from #103) ----------------------

def test_today_opens_an_unread_inbox_mail_while_mail_shows_sent(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1, True), row(2)],
                                  "inbox_unread": summary(1, row(1, True))})
    s.set_mail_scope("sent")
    answer(data, "emails.list", {"emails": [row(3, labels=("SENT",))],
                                 "inbox_unread": summary(1, row(1, True))})
    s.open_mail("m1")                     # Today's unread row
    assert s.mail_scope == "inbox"
    answer(data, "emails.list", {"emails": [row(1, True), row(2)],
                                 "inbox_unread": summary(1, row(1, True))})
    assert s.selected_mail == "m1"        # not whichever row came first
    assert not s.sel_mail()["unread"]
    assert data.last("emails.auto_read")[0] == {"id": "m1"}


def test_open_mail_in_the_list_just_opens_it(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1, True), row(2, True)]})
    s.open_mail("m2")
    assert s.selected_mail == "m2" and not s.sel_mail()["unread"]
    assert data.request_types()[-1] != "emails.list"


def test_open_mail_ends_a_search(st):
    s, data = st
    answer(data, "mail.refresh", {"emails": [row(1, True)]})
    s.search_mails("invoice")
    answer(data, "emails.search", {"emails": []})
    s.open_mail("m1")
    assert s.mail_query == ""
    assert data.request_types()[-1] == "emails.list"
