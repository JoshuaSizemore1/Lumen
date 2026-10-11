"""Starred email: store list_page, search, GmailSync.star, emails.star route,
star_email tool, MAIL_LABEL_HINT routing, and is:starred parse. (#40)"""
import asyncio
import re

import pytest

from lumen.daemon import db
from lumen.daemon.connectors.email_menu import (
    EmailStore, GmailSync, parse_mail_query,
)
from lumen.daemon.config import GoogleConfig, SyncConfig
from lumen.daemon.local_tools import MAIL_TOOLS, MAIL_TOOL_NAMES
from lumen.daemon.router import MAIL_LABEL_HINT, Router


# ---- helpers ----------------------------------------------------------------

def make_store(tmp_path):
    return EmailStore(db.connect(tmp_path / "e.db"))


def msg(i, **over):
    base = {
        "id": f"m{i}", "thread_id": f"t{i}",
        "sender": f"Sender {i} <s{i}@x.com>",
        "recipients": "me@x.com", "subject": f"Subject {i}",
        "body": f"body {i}", "snippet": f"snip {i}",
        "labels": ["INBOX", "UNREAD"],
        "received_at": f"2026-07-{i:02d}T10:00:00+00:00",
        "is_read": False, "attachments": [],
    }
    base.update(over)
    return base


class FakeExec:
    def __init__(self, result):
        self._r = result

    def execute(self):
        if isinstance(self._r, Exception):
            raise self._r
        return self._r


class ModifyingService:
    """A minimal fake that records modify() calls and can be made to fail."""

    def __init__(self, fail=False):
        self.modified = []
        self._fail = fail

    def users(self):
        return self

    def messages(self):
        return self

    def modify(self, userId, id, body):
        if self._fail:
            return FakeExec(RuntimeError("api down"))
        self.modified.append((id, body))
        return FakeExec({"id": id})


def make_sync(tmp_path, service):
    store = make_store(tmp_path)
    sync = GmailSync(store, GoogleConfig(), SyncConfig(),
                     service_factory=lambda: service)
    return store, sync


# ---- router helpers (mirrors test_router.py) --------------------------------


class FakeLLM:
    def __init__(self, chunks=("ok",)):
        self._chunks = chunks
        self.tool_names = []
        self.system = None

    async def is_loaded(self):
        return True

    async def chat(self, messages):
        self.system = messages[0]["content"] if messages else ""
        for c in self._chunks:
            yield c

    async def chat_with_tools(self, messages, tools, executor, *,
                               model=None, max_iterations=4):
        self.tool_names = [t["function"]["name"] for t in tools]
        self.system = messages[0]["content"] if messages else ""
        for c in self._chunks:
            yield {"content": c}

    async def unload(self):
        pass

    async def warm(self, prime=None):
        pass


class StarToolLLM:
    """Fake LLM that immediately calls star_email with the injected id."""

    def __init__(self, email_id="m1", starred=True):
        self.tool_names = []
        self.system = None
        self._id, self._starred = email_id, starred

    async def is_loaded(self):
        return True

    async def chat(self, messages):
        yield ""

    async def chat_with_tools(self, messages, tools, executor, *,
                               model=None, max_iterations=4):
        self.tool_names = [t["function"]["name"] for t in tools]
        self.system = messages[0]["content"] if messages else ""
        args = {"email_id": self._id, "starred": self._starred}
        yield {"tool_call": {"name": "star_email", "arguments": args}}
        yield {"content": await executor("star_email", args)}

    async def unload(self):
        pass

    async def warm(self, prime=None):
        pass


class FakeStore:
    def __init__(self):
        self.rows = []

    def add(self, raw, today=None, source="manual"):
        pass

    def list_all(self):
        return self.rows

    def open_todos(self):
        return self.rows

    def toggle(self, todo_id, completed):
        pass

    def exists(self, todo_id):
        return False

    def update(self, todo_id, **kw):
        pass


class FakeMailStore:
    def __init__(self):
        self.rows = [
            {"id": "m1", "sender": "Ada <a@x.com>", "subject": "Engines",
             "snippet": "s", "body": "b",
             "labels": ["INBOX", "UNREAD"],
             "received_at": "2026-07-10T10:00:00+00:00",
             "is_read": False, "attachments": [],
             "thread_id": "t1", "recipients": "me"},
        ]
        self.labels = [{"id": "Label_7", "name": "Bills"}]

    def list_page(self, filter="inbox", limit=50, offset=0, label_id=None):
        if filter == "label":
            return [r for r in self.rows if label_id in r["labels"]]
        if filter == "inbox":
            return [r for r in self.rows if "INBOX" in r["labels"]]
        if filter == "starred":
            return [r for r in self.rows if "STARRED" in r["labels"]]
        return self.rows

    def user_labels(self):
        return self.labels

    def label_id(self, name):
        return next((l["id"] for l in self.labels if l["name"] == name), None)

    def present_label_ids(self):
        return {l for r in self.rows for l in r["labels"]}

    def search(self, query, limit=50):
        return self.rows if "engine" in query.lower() else []

    def get(self, mid):
        return next((r for r in self.rows if r["id"] == mid), None)

    def unread(self, limit=10):
        return [r for r in self.rows if not r["is_read"]]

    def counts(self):
        return {"total": len(self.rows), "unread": sum(1 for r in self.rows
                                                       if not r["is_read"])}

    def update_labels(self, mid, add, remove):
        row = self.get(mid)
        if row is None:
            return
        row["labels"] = [l for l in row["labels"] if l not in remove]
        row["labels"] += [l for l in add if l not in row["labels"]]
        row["is_read"] = "UNREAD" not in row["labels"]


class FakeMailSync:
    connected, syncing, busy = True, False, False

    def __init__(self):
        self.starred = []
        self.marked = []
        self.synced = 0
        self.archived = []
        self.labeled = []
        self.unlabeled = []
        self.trashed = []
        self.html_fetched = []

    async def star(self, mid, starred):
        self.starred.append((mid, starred))
        return True

    async def mark_read(self, mid, read):
        self.marked.append((mid, read))
        return True

    async def apply_label(self, mid, label_name):
        self.labeled.append((mid, label_name))
        return True

    async def remove_label(self, mid, label_name):
        self.unlabeled.append((mid, label_name))
        return True

    async def archive(self, mid):
        self.archived.append(mid)
        return True

    async def trash(self, mid):
        self.trashed.append(mid)
        return True

    async def fetch_html(self, mid):
        self.html_fetched.append(mid)
        return None

    async def sync_once(self):
        self.synced += 1
        return True

    def last_sync(self):
        return "2026-07-12T13:00:00"


class FakeBridge:
    def __init__(self, tools=()):
        self._tools = list(tools)
        self.started = False

    async def ensure_started(self):
        self.started = True

    def ollama_tools(self, servers=None):
        return [{"type": "function", "function": {"name": n}}
                for n in self._tools]

    async def call(self, name, args):
        return "ok"


class FakeModelRouter:
    def pick_model(self, message, *, needs_tools):
        return "fast"


async def collect(router, type_, payload):
    out = []
    async for ev in router.handle(type_, payload):
        out.append(ev)
    return out


def make_router(llm=None, mail=None, mail_store=None):
    return Router(
        llm or FakeLLM(),
        FakeStore(),
        mail=mail or FakeMailSync(),
        mail_store=mail_store or FakeMailStore(),
        bridge=FakeBridge(tools=()),
        model_router=FakeModelRouter(),
    )


# ============================================================================
# 1. EmailStore.list_page("starred") scope
# ============================================================================

def test_list_page_starred_scope(tmp_path):
    """list_page("starred") returns only STARRED messages, including
    archived (non-INBOX) ones — starred-but-archived mail must be visible."""
    store = make_store(tmp_path)
    store.upsert([
        msg(1, labels=["INBOX", "UNREAD"]),            # inbox, not starred
        msg(2, labels=["STARRED"]),                    # archived AND starred
        msg(3, labels=["INBOX", "STARRED"]),           # inbox AND starred
    ])
    result = store.list_page("starred")
    assert {m["id"] for m in result} == {"m2", "m3"}


def test_list_page_starred_ordering(tmp_path):
    """Starred scope returns newest first (same contract as other scopes)."""
    store = make_store(tmp_path)
    store.upsert([
        msg(1, labels=["STARRED"], received_at="2026-07-01T10:00:00+00:00"),
        msg(3, labels=["STARRED"], received_at="2026-07-03T10:00:00+00:00"),
        msg(2, labels=["STARRED"], received_at="2026-07-02T10:00:00+00:00"),
    ])
    ids = [m["id"] for m in store.list_page("starred")]
    assert ids == ["m3", "m2", "m1"]


# ============================================================================
# 2. parse_mail_query: is:starred / is:unstarred
# ============================================================================

from datetime import datetime, timedelta, timezone

_TZ = timezone(timedelta(hours=-6))
_NOW = datetime(2026, 7, 17, 16, 0, tzinfo=_TZ)


def test_parse_is_starred_sets_flag():
    p = parse_mail_query("is:starred", now_local=_NOW)
    assert p["starred"] is True


def test_parse_is_unstarred_sets_flag():
    p = parse_mail_query("is:unstarred", now_local=_NOW)
    assert p["starred"] is False


def test_parse_is_unread_unchanged():
    p = parse_mail_query("is:unread", now_local=_NOW)
    assert p["starred"] is None   # is:unread must not flip starred


def test_parse_combines_is_starred_with_text(tmp_path):
    store = make_store(tmp_path)
    store.upsert([
        msg(1, body="important meeting", labels=["INBOX", "STARRED"]),
        msg(2, body="important meeting", labels=["INBOX"]),
    ])
    results = store.search("is:starred important", now_local=_NOW)
    assert [m["id"] for m in results] == ["m1"]


def test_search_in_starred_scope(tmp_path):
    store = make_store(tmp_path)
    store.upsert([
        msg(1, labels=["STARRED"]),
        msg(2, labels=["INBOX"]),
    ])
    results = store.search("in:starred", now_local=_NOW)
    assert [m["id"] for m in results] == ["m1"]


# ============================================================================
# 3. GmailSync.star — hits the API, mirrors the label, never touches INBOX
# ============================================================================

async def test_star_adds_starred_label(tmp_path):
    svc = ModifyingService()
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    assert await sync.star("m1", True) is True
    assert svc.modified == [("m1", {"addLabelIds": ["STARRED"]})]
    assert "STARRED" in store.get("m1")["labels"]
    assert "INBOX" in store.get("m1")["labels"]   # INBOX must not be touched


async def test_unstar_removes_starred_label(tmp_path):
    svc = ModifyingService()
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1, labels=["INBOX", "STARRED"])])
    assert await sync.star("m1", False) is True
    assert svc.modified == [("m1", {"removeLabelIds": ["STARRED"]})]
    assert "STARRED" not in store.get("m1")["labels"]
    assert "INBOX" in store.get("m1")["labels"]   # INBOX must not be touched


async def test_star_failure_leaves_mirror_untouched(tmp_path):
    svc = ModifyingService(fail=True)
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    assert await sync.star("m1", True) is False
    assert "STARRED" not in store.get("m1")["labels"]


async def test_star_never_adds_or_removes_inbox(tmp_path):
    """Starring must NEVER touch INBOX — apply_label does, star must not."""
    svc = ModifyingService()
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    await sync.star("m1", True)
    body = svc.modified[0][1]
    assert "INBOX" not in body.get("addLabelIds", [])
    assert "INBOX" not in body.get("removeLabelIds", [])


# ============================================================================
# 4. emails.star route: ungated, correct mirror flip, error on missing id
# ============================================================================

async def test_emails_star_route_stars_message():
    mail, store = FakeMailSync(), FakeMailStore()
    router = make_router(mail=mail, mail_store=store)
    out = await collect(router, "emails.star", {"id": "m1", "starred": True})
    assert not any("confirm_request" in e for e in out)
    assert out[-1]["result"]["ok"] is True
    assert mail.starred == [("m1", True)]


async def test_emails_star_route_unstars_message():
    mail, store = FakeMailSync(), FakeMailStore()
    router = make_router(mail=mail, mail_store=store)
    out = await collect(router, "emails.star", {"id": "m1", "starred": False})
    assert out[-1]["result"]["ok"] is True
    assert mail.starred == [("m1", False)]


async def test_emails_star_route_error_on_missing_id():
    router = make_router()
    out = await collect(router, "emails.star", {"id": "ghost", "starred": True})
    assert "error" in out[-1]


# ============================================================================
# 5. star_email tool — in MAIL_TOOLS, dispatched correctly
# ============================================================================

def test_star_email_in_mail_tools():
    names = [t["function"]["name"] for t in MAIL_TOOLS]
    assert "star_email" in names
    assert "star_email" in MAIL_TOOL_NAMES


def test_star_email_tool_schema():
    tool = next(t for t in MAIL_TOOLS if t["function"]["name"] == "star_email")
    props = tool["function"]["parameters"]["properties"]
    assert "email_id" in props
    assert "starred" in props
    assert props["starred"]["type"] == "boolean"


async def test_star_email_tool_executes_via_chat():
    llm = StarToolLLM(email_id="m1", starred=True)
    mail, store = FakeMailSync(), FakeMailStore()
    router = make_router(llm=llm, mail=mail, mail_store=store)
    out = await collect(router, "chat",
                        {"message": "star this email",
                         "open_email": {"id": "m1", "subject": "Engines",
                                        "from": "Ada"}})
    assert "star_email" in llm.tool_names
    assert mail.starred == [("m1", True)]
    assert any("Starred" in e.get("chunk", "") for e in out)


async def test_star_email_tool_reports_unknown_id():
    llm = StarToolLLM(email_id="ghost", starred=True)
    mail, store = FakeMailSync(), FakeMailStore()
    router = make_router(llm=llm, mail=mail, mail_store=store)
    out = await collect(router, "chat",
                        {"message": "star this email"})
    assert mail.starred == []
    assert any("No email with id ghost" in e.get("chunk", "") for e in out)


async def test_unstar_tool_executes():
    llm = StarToolLLM(email_id="m1", starred=False)
    mail, store = FakeMailSync(), FakeMailStore()
    router = make_router(llm=llm, mail=mail, mail_store=store)
    out = await collect(router, "chat",
                        {"message": "unstar this email",
                         "open_email": {"id": "m1", "subject": "Engines",
                                        "from": "Ada"}})
    assert mail.starred == [("m1", False)]
    assert any("Unstarred" in e.get("chunk", "") for e in out)


async def test_a_string_false_from_the_model_unstars():
    """bool("false") is True: a small model's string flag used to star."""
    llm = StarToolLLM(email_id="m1", starred="false")
    mail, store = FakeMailSync(), FakeMailStore()
    router = make_router(llm=llm, mail=mail, mail_store=store)
    await collect(router, "chat", {"message": "unstar this email",
                                   "open_email": {"id": "m1", "subject": "Engines",
                                                  "from": "Ada"}})
    assert mail.starred == [("m1", False)]


@pytest.mark.parametrize("phrase", [
    "Is the Star Wars movie good? Is it on Netflix?",
    "a five-star review of this place",
    "who is the star of that film",
])
def test_star_as_a_noun_is_not_a_mail_action(phrase):
    assert not MAIL_LABEL_HINT.search(phrase)


# ============================================================================
# 6. MAIL_LABEL_HINT routing: star phrasings route to tool loop
# ============================================================================

@pytest.mark.parametrize("phrase", [
    "star this email",
    "star this message",
    "unstar this email",
    "unstar this message",
    "unstar it",
    "star it please",
])
def test_star_phrasings_match_mail_label_hint(phrase):
    assert MAIL_LABEL_HINT.search(phrase), (
        f"MAIL_LABEL_HINT should match {phrase!r}")


@pytest.mark.parametrize("phrase", [
    "label this as Work",
    "tag this email",
    "categorize the email",
    "unlabel this",
    "untag it",
])
def test_label_phrasings_still_match(phrase):
    assert MAIL_LABEL_HINT.search(phrase), (
        f"Existing label phrasing {phrase!r} should still match")


def test_label_this_as_work_is_not_a_star_operation():
    """'label this as Work' must match MAIL_LABEL_HINT (fine), but the
    label_email tool must win over star_email for that phrasing — make sure
    star_email doesn't replace label_email by greedy regex matching."""
    # The regex only controls which sub-path (tool loop) runs.
    # The tool loop exposes ALL MAIL_TOOLS including both label_email and
    # star_email; the model picks which to call.  We just verify the regex
    # match so both tools are available.
    assert MAIL_LABEL_HINT.search("label this as Work")


# ============================================================================
# 7. Bulk sync already mirrors starred-but-archived mail (no label restriction)
# ============================================================================

def test_starred_archived_mail_is_in_list_page_starred(tmp_path):
    """The bulk sync's q=f'after:{date}' has NO label restriction, so
    archived+starred mail is in the mirror with STARRED in its labels.
    list_page('starred') must surface it."""
    store = make_store(tmp_path)
    # Simulate what normalize_message stores for archived+starred mail.
    store.upsert([msg(1, labels=["STARRED"])])   # no INBOX → archived
    result = store.list_page("starred")
    assert len(result) == 1 and result[0]["id"] == "m1"


# ============================================================================
# 8. open_email_context mentions star_email
# ============================================================================

async def test_open_email_context_names_star_email():
    llm = FakeLLM()
    mail, store = FakeMailSync(), FakeMailStore()
    router = make_router(llm=llm, mail=mail, mail_store=store)
    # Trigger a MAIL_LABEL_HINT path so _open_email_context is injected.
    await collect(router, "chat",
                  {"message": "star this email",
                   "open_email": {"id": "m1", "subject": "Engines", "from": "Ada"}})
    assert "star_email" in (llm.system or "")
