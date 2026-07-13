# Phase 7 (part 1) — Email Compose & Send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A compose popup (Mail-screen Compose/Reply buttons + chat-driven pre-filled drafts) that sends through the user's Gmail, with an in-popup "Ask Lumen to revise" loop — nothing sends except an explicit Send click.

**Architecture:** Mirrors the calendar NL-creation path: a routing hint sends compose-shaped chat to one structured fast-model extraction, the daemon emits a `compose_request` over IPC and awaits the popup's answer through the (generalized) ConfirmBroker, then sends via a new `GmailSync.send()`. Popup-initiated sends go through a plain `emails.send` one-shot — the popup itself is the write confirmation.

**Tech Stack:** Python 3.12, asyncio daemon, PyQt6 UI, Gmail API (`gmail.modify` scope — already granted, authorizes send), pytest + pytest-qt.

## Global Constraints

- No auto-send, ever: `GmailSync.send` may only be reached from `emails.send` / `compose.response` (both downstream of an explicit Send click in the popup). No confirm-overlay double gate on top.
- The model may not invent recipients: pre-filled To/Cc addresses must appear verbatim in the user's message, or be the replied-to sender from the local mirror.
- All LLM calls stay in `daemon/llm/`; fast model only; no tools in the compose/revise generations.
- Commit messages end with `This commit used N prompts.` and carry no Co-Authored-By line.
- Attachments, HTML mail, Gmail drafts, style-ruleset derivation: out of scope.

---

### Task 1: ConfirmBroker carries payloads and per-call timeouts

**Files:**
- Modify: `lumen/daemon/confirm.py` (wait, resolve)
- Test: `tests/daemon/test_confirm.py`

**Interfaces:**
- Produces: `wait(confirm_id, timeout: float | None = None) -> Any` (False on timeout/deny; otherwise whatever `resolve` passed), `resolve(confirm_id, result) -> bool` (no bool coercion). Existing bool callers unchanged (router's confirm.response already coerces).

- [ ] **Step 1: failing tests**

```python
async def test_resolve_passes_payload_through():
    broker = ConfirmBroker()
    cid = broker.begin()
    task = asyncio.ensure_future(broker.wait(cid))
    await asyncio.sleep(0)
    assert broker.resolve(cid, {"subject": "s"}) is True
    assert await task == {"subject": "s"}


async def test_wait_per_call_timeout_overrides_default():
    broker = ConfirmBroker(timeout=60)
    cid = broker.begin()
    assert await broker.wait(cid, timeout=0.01) is False
```

- [ ] **Step 2: run** `uv run pytest tests/daemon/test_confirm.py -q` — 2 new FAIL (TypeError / payload coerced to True).
- [ ] **Step 3: implement** — `wait(self, confirm_id, timeout=None)` using `self._timeout if timeout is None else timeout`; `resolve` does `fut.set_result(result)` (param renamed `approved` → `result`, no `bool()`); update both docstrings (wait: "the user's answer — False on timeout/disconnect, else whatever resolve carried").
- [ ] **Step 4: run** same file — all PASS.
- [ ] **Step 5: commit** `Let the confirm broker carry payloads and per-call timeouts`

### Task 2: `daemon/llm/email_compose.py` — draft + revise generations

**Files:**
- Create: `lumen/daemon/llm/email_compose.py`
- Test: `tests/daemon/llm/test_email_compose.py`

**Interfaces:**
- Consumes: `parse_proposal` from `lumen.daemon.llm.event_create` (balanced-JSON extraction).
- Produces: `EMAIL` (compiled regex), `propose_email(llm, message) -> tuple[dict | None, str | None]` (draft dict `{to: list, cc: list, subject: str, body: str, reply_hint: str | None}` or honest error), `validate_draft(p, *, user_message) -> dict`, `revise_email(llm, subject, body, instruction) -> tuple[dict | None, str | None]` (`{subject, body}` or error).

- [ ] **Step 1: failing tests** (FakeLLM: `async def chat(self, messages)` yielding canned chunks, same shape as `tests/daemon/test_router.py`)

```python
"""email_compose: extraction gate — editable-draft softness, no invented recipients."""
from lumen.daemon.llm.email_compose import propose_email, revise_email, validate_draft


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.messages = reply, None

    async def chat(self, messages):
        self.messages = messages
        yield self.reply


def test_validate_draft_keeps_only_addresses_the_user_wrote():
    p = {"to": ["sam@x.com", "made.up@spam.io", "Sam"], "cc": ["sam@x.com"],
         "subject": " hi ", "body": " text ", "reply_hint": ""}
    d = validate_draft(p, user_message="email sam@x.com please")
    assert d["to"] == ["sam@x.com"]          # invented + non-address dropped, not fatal
    assert d["cc"] == ["sam@x.com"]
    assert d["subject"] == "hi" and d["body"] == "text"
    assert d["reply_hint"] is None


def test_validate_draft_missing_keys_yield_editable_empties():
    d = validate_draft({}, user_message="whatever")
    assert d == {"to": [], "cc": [], "subject": "", "body": "", "reply_hint": None}


async def test_propose_email_parses_json_reply():
    llm = FakeLLM('{"to": ["a@b.co"], "cc": [], "subject": "S", "body": "B", '
                  '"reply_hint": "Ada engines"}')
    draft, err = await propose_email(llm, "reply to ada's engines email, a@b.co")
    assert err is None and draft["to"] == ["a@b.co"]
    assert draft["reply_hint"] == "Ada engines"
    assert "JSON" in llm.messages[0]["content"]


async def test_propose_email_unparseable_is_honest():
    draft, err = await propose_email(FakeLLM("sure, sending it now!"), "email bob")
    assert draft is None and "draft" in err


async def test_revise_email_returns_full_revision():
    llm = FakeLLM('{"subject": "Shorter", "body": "Hi."}')
    got, err = await revise_email(llm, "Long subject", "Long body", "shorter")
    assert err is None and got == {"subject": "Shorter", "body": "Hi."}
    assert "Long body" in llm.messages[1]["content"]


async def test_revise_email_empty_body_fails():
    got, err = await revise_email(FakeLLM('{"subject": "s", "body": ""}'), "s", "b", "i")
    assert got is None and "revision" in err
```

- [ ] **Step 2: run** `uv run pytest tests/daemon/llm/test_email_compose.py -q` — FAIL (module missing).
- [ ] **Step 3: implement**

```python
"""NL email drafting: fast-model extraction into an editable draft, then a
mechanical gate. Deliberately softer than event_create's — the compose popup
is fully editable and nothing sends without the user clicking Send, so a bad
address is dropped for the user to fill in rather than a hard refusal. The
one hard rule survives: a pre-filled recipient must be an address the user
literally wrote (the reply path adds the mirrored sender, router-side)."""

import re

from lumen.daemon.llm.event_create import parse_proposal

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DRAFT_SYSTEM = (
    "You draft an email from the user's request. Reply with ONLY a JSON "
    "object, no prose, shaped exactly:\n"
    '{"to": [], "cc": [], "subject": "...", "body": "...", "reply_hint": null}\n'
    "Rules: to/cc may ONLY contain email addresses the user explicitly wrote — "
    "a name is not an address; leave to empty and the user will fill it in. "
    "body is the complete ready-to-send email text in the user's voice, with "
    "a simple sign-off and no [placeholders]. If the user is replying to an "
    "email they received, reply_hint is a few words identifying it (sender "
    "name and/or subject words); otherwise null."
)

REVISE_SYSTEM = (
    "You revise an email draft according to the user's instruction. Reply "
    'with ONLY a JSON object, no prose, shaped exactly: '
    '{"subject": "...", "body": "..."} — the complete revised draft, not a '
    "diff. Keep everything the instruction doesn't ask you to change."
)


def validate_draft(p: dict, *, user_message: str) -> dict:
    """Mechanical gate. Recipients not literally in the user's message are
    dropped (never fatal); strings trimmed; always an editable draft."""
    def addresses(raw):
        ok, haystack = [], (user_message or "").casefold()
        for a in raw or []:
            a = str(a).strip()
            if EMAIL.match(a) and a.casefold() in haystack and a not in ok:
                ok.append(a)
        return ok
    return {"to": addresses(p.get("to")), "cc": addresses(p.get("cc")),
            "subject": str(p.get("subject") or "").strip(),
            "body": str(p.get("body") or "").strip(),
            "reply_hint": str(p.get("reply_hint") or "").strip() or None}


async def _generate(llm, system: str, user: str) -> dict | None:
    text = ""
    async for chunk in llm.chat([{"role": "system", "content": system},
                                 {"role": "user", "content": user}]):
        text += chunk
    return parse_proposal(text)


async def propose_email(llm, message: str) -> tuple[dict | None, str | None]:
    """One structured generation on the fast model — no tools, no chain."""
    raw = await _generate(llm, DRAFT_SYSTEM, message)
    if raw is None:
        return None, ("I couldn't put a draft together from that — tell me "
                      "who it's for and roughly what to say.")
    return validate_draft(raw, user_message=message), None


async def revise_email(llm, subject: str, body: str,
                       instruction: str) -> tuple[dict | None, str | None]:
    raw = await _generate(llm, REVISE_SYSTEM,
                          f"Subject: {subject}\n\n{body}\n\nInstruction: {instruction}")
    if raw is None or not str(raw.get("body") or "").strip():
        return None, "revision failed — try rephrasing the instruction"
    return {"subject": str(raw.get("subject") or subject).strip(),
            "body": str(raw["body"]).strip()}, None
```

- [ ] **Step 4: run** — all PASS.
- [ ] **Step 5: commit** `Add the email draft and revise generations`

### Task 3: `GmailSync.send()` with reply threading

**Files:**
- Modify: `lumen/daemon/connectors/email_menu.py` (after `mark_read`; add `from email.message import EmailMessage` import)
- Test: `tests/daemon/connectors/test_email_menu.py`

**Interfaces:**
- Consumes: existing `_build_service(write=True)` / injected `service_factory`, `EmailStore.get`.
- Produces: `GmailSync.send(to: list[str], cc: list[str], bcc: list[str], subject: str, body: str, *, reply_to: str | None = None) -> bool`.

- [ ] **Step 1: failing tests** (fresh minimal fake — the sync `FakeService` signature doesn't fit metadata gets)

```python
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
```

- [ ] **Step 2: run** `uv run pytest tests/daemon/connectors/test_email_menu.py -q -k send` — FAIL (no attribute `send`).
- [ ] **Step 3: implement** (mirror lookup on the loop thread; API calls in `to_thread` like `_modify`)

```python
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
```

- [ ] **Step 4: run** whole file — PASS (including all pre-existing sync tests).
- [ ] **Step 5: commit** `Add GmailSync.send with reply threading`

### Task 4: Router — compose chat path, emails.send/revise, compose.response, identity line

**Files:**
- Modify: `lumen/daemon/router.py`
- Test: `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: Task 1 broker (`wait(id, timeout=)`, payload `resolve`), Task 2 (`EMAIL`, `propose_email`, `revise_email`), Task 3 (`GmailSync.send`).
- Produces: IPC — `compose_request`/`compose_id` chat event with fields `{to, cc, bcc, subject, body, reply_to}`; one-shots `emails.send {to, cc, bcc, subject, body, reply_to}` and `emails.revise {subject, body, instruction}` → `{result}`; `compose.response {compose_id, send: bool, fields}` → `{result: {ok, message}}`.

- [ ] **Step 1: failing tests**

```python
class SendingMailSync(FakeMailSync):
    def __init__(self, ok=True):
        super().__init__()
        self.sent, self._ok = [], ok

    async def send(self, to, cc, bcc, subject, body, reply_to=None):
        self.sent.append((to, cc, bcc, subject, body, reply_to))
        return self._ok


def compose_router(llm, sync=None, store=None):
    return Router(llm, FakeStore(), mail=sync or SendingMailSync(),
                  mail_store=store or FakeMailStore(), confirm=ConfirmBroker())


def test_compose_hint_shapes():
    from lumen.daemon.router import COMPOSE_HINT
    hits = ["send an email to sam@x.com about friday",
            "write an email telling the team we shipped",
            "reply to Ada's email saying thanks",
            "email Sarah about rescheduling"]
    misses = ["did sarah email me back?", "any new email today?",
              "search my email for invoices", "how many unread emails"]
    assert all(COMPOSE_HINT.search(h) for h in hits)
    assert not any(COMPOSE_HINT.search(m) for m in misses)


DRAFT_JSON = ('{"to": ["sam@x.com"], "cc": [], "subject": "Friday", '
              '"body": "Hi Sam", "reply_hint": null}')


async def _drive_compose(router, message, respond):
    """Collect the compose stream, answering the popup via `respond(request)`."""
    out = []
    async for ev in router.handle("chat", {"message": message}):
        out.append(ev)
        if "compose_request" in ev:
            respond(ev)
    return out


async def test_chat_compose_send_roundtrip():
    sync = SendingMailSync()
    router = compose_router(FakeLLM((DRAFT_JSON,)), sync)

    def respond(ev):
        assert ev["compose_request"]["to"] == ["sam@x.com"]
        router._confirm.resolve(ev["compose_id"],
                                {"to": ["sam@x.com"], "cc": [], "bcc": [],
                                 "subject": "Friday (edited)", "body": "Hi Sam!",
                                 "reply_to": None})
    out = await _drive_compose(router, "send an email to sam@x.com about friday", respond)
    assert sync.sent == [(["sam@x.com"], [], [], "Friday (edited)", "Hi Sam!", None)]
    text = "".join(e.get("chunk", "") for e in out)
    assert "Sent." in text and {"done": True} in out


async def test_chat_compose_cancel_sends_nothing():
    sync = SendingMailSync()
    router = compose_router(FakeLLM((DRAFT_JSON,)), sync)
    out = await _drive_compose(
        router, "send an email to sam@x.com",
        lambda ev: router._confirm.resolve(ev["compose_id"], False))
    assert sync.sent == []
    assert "Cancelled" in "".join(e.get("chunk", "") for e in out)


async def test_chat_compose_reply_hint_prefills_from_mirror():
    reply_json = ('{"to": [], "cc": [], "subject": "", "body": "Thanks!", '
                  '"reply_hint": "Ada engines"}')
    router = compose_router(FakeLLM((reply_json,)))
    seen = {}
    out = await _drive_compose(
        router, "reply to ada's email about engines saying thanks",
        lambda ev: (seen.update(ev["compose_request"]),
                    router._confirm.resolve(ev["compose_id"], False)))
    assert seen["to"] == ["a@x.com"]            # FakeMailStore sender Ada <a@x.com>
    assert seen["subject"] == "Re: Engines" and seen["reply_to"] == "m1"


async def test_chat_compose_unparseable_draft_is_honest_chat():
    router = compose_router(FakeLLM(("no json here",)))
    out = await collect(router, "chat", {"message": "send an email to sam@x.com"})
    assert not any("compose_request" in e for e in out)
    assert "draft" in "".join(e.get("chunk", "") for e in out)


async def test_emails_send_oneshot_validates_and_sends():
    sync = SendingMailSync()
    router = compose_router(FakeLLM(), sync)
    out = await collect(router, "emails.send",
                        {"to": ["a@x.com"], "cc": [], "bcc": [],
                         "subject": "s", "body": "b"})
    assert out == [{"result": {"ok": True, "message": "Sent."}}]
    bad = await collect(router, "emails.send",
                        {"to": ["not-an-address"], "subject": "s", "body": "b"})
    assert bad[0]["result"]["ok"] is False and sync.sent[-1][0] == ["a@x.com"]
    empty = await collect(router, "emails.send", {"to": [], "body": "b"})
    assert "recipient" in empty[0]["result"]["message"]


async def test_emails_revise_oneshot():
    router = compose_router(FakeLLM(('{"subject": "S2", "body": "B2"}',)))
    out = await collect(router, "emails.revise",
                        {"subject": "S", "body": "B", "instruction": "shorter"})
    assert out == [{"result": {"subject": "S2", "body": "B2"}}]


async def test_compose_response_expired_id_still_sends():
    # The popup outlived the chat wait: a Send click must not be dropped.
    sync = SendingMailSync()
    router = compose_router(FakeLLM(), sync)
    out = await collect(router, "compose.response",
                        {"compose_id": 999, "send": True,
                         "fields": {"to": ["a@x.com"], "subject": "s", "body": "b"}})
    assert sync.sent and out[0]["result"]["ok"] is True


async def test_identity_owns_sending():
    from lumen.daemon.router import IDENTITY
    assert "compose window" in IDENTITY
```

- [ ] **Step 2: run** `uv run pytest tests/daemon/test_router.py -q -k "compose or emails_send or emails_revise or identity_owns_sending"` — FAIL.
- [ ] **Step 3: implement** in `router.py`:

Imports: `from lumen.daemon.llm.email_compose import (EMAIL, propose_email, revise_email)`.

Constants (near EVENT_HINT):

```python
# Compose-shaped requests jump to the draft → popup path before every other
# chat route (EVENT_HINT would steal "draft an email to schedule a meeting").
# Read-shaped mail questions ("did Sam email me back?") must NOT match.
COMPOSE_HINT = re.compile(
    r"\b(?:send|write|draft|compose|shoot)\b.{0,60}\b(?:e-?mails?|reply|message)\b"
    r"|\breply(?:ing)?\b.{0,60}\b(?:e-?mails?|saying|telling|that)\b"
    r"|\be-?mail\b.{0,40}\b(?:to|saying|telling|asking|about)\b",
    re.IGNORECASE | re.DOTALL,
)

# A compose wait is an edit session, not a confirm click.
COMPOSE_TIMEOUT_S = 1800.0
```

IDENTITY — append before "Prefer specific, concise answers.":
`"When the user asks you to write, send, or reply to an email, a compose window opens with your draft for them to review and send — so never claim you can't send email. "`

Module helper:

```python
def _sender_address(sender: str) -> str | None:
    """'Ada Lovelace <a@x.com>' or bare 'a@x.com' -> the address."""
    m = re.search(r"<([^<>@\s]+@[^<>@\s]+)>", sender or "")
    if m:
        return m.group(1)
    s = (sender or "").strip()
    return s if EMAIL.match(s) else None
```

`_chat` — first branch, above EVENT_HINT:

```python
        if (self._confirm is not None and self._mail is not None
                and COMPOSE_HINT.search(message)):
            sub = self._compose_email_chat(message)
        elif (self._confirm is not None and self._bridge is not None
              ...
```

New methods:

```python
    async def _compose_email_chat(self, message: str):
        """NL draft → editable compose popup → send/cancel. The popup is the
        confirmation: the daemon sends exactly the fields the UI returns, and
        only on an explicit Send."""
        try:
            draft, err = await propose_email(self._llm, message)
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if draft is None:
            yield {"chunk": err}     # honest failure is an answer, not an IPC error
            yield {"done": True}
            return
        reply_to = None
        if draft["reply_hint"] and self._mail_store is not None:
            hits = sorted(self._mail_store.search(draft["reply_hint"], limit=5),
                          key=lambda r: r.get("received_at") or "", reverse=True)
            if hits:            # newest plausible match; miss = plain compose
                orig = hits[0]
                reply_to = orig["id"]
                addr = _sender_address(orig.get("sender", ""))
                if addr:
                    draft["to"] = [addr]
                subj = orig.get("subject") or ""
                draft["subject"] = (subj if subj.lower().startswith("re:")
                                    else f"Re: {subj}")
        compose_id = self._confirm.begin()
        yield {"compose_request": {"to": draft["to"], "cc": draft["cc"], "bcc": [],
                                   "subject": draft["subject"], "body": draft["body"],
                                   "reply_to": reply_to},
               "compose_id": compose_id}
        yield {"chunk": "I've drafted it — review the compose window and hit "
                        "Send when it's right."}
        answer = await self._confirm.wait(compose_id, timeout=COMPOSE_TIMEOUT_S)
        if not isinstance(answer, dict):
            yield {"chunk": "\n\nCancelled — nothing was sent."}
            yield {"done": True}
            return
        _ok, text = await self._send_email(answer)
        yield {"chunk": f"\n\n{text}"}
        yield {"done": True}

    async def _send_email(self, fields: dict) -> tuple[bool, str]:
        """Validate + send. No confirm gate: every caller is downstream of the
        compose popup, whose Send click is the confirmation."""
        def addrs(key):
            out = []
            for a in fields.get(key) or []:
                a = str(a).strip()
                if a and not EMAIL.match(a):
                    raise ValueError(f"{a!r} isn't a valid email address — "
                                     "nothing was sent.")
                if a and a not in out:
                    out.append(a)
            return out
        try:
            to, cc, bcc = addrs("to"), addrs("cc"), addrs("bcc")
        except ValueError as e:
            return False, str(e)
        if not to:
            return False, "No valid recipient — nothing was sent."
        body = str(fields.get("body") or "").strip()
        if not body:
            return False, "The email body is empty — nothing was sent."
        subject = str(fields.get("subject") or "").strip()
        ok = await self._mail.send(to, cc, bcc, subject, body,
                                   reply_to=fields.get("reply_to") or None)
        return ((True, "Sent.") if ok
                else (False, "Couldn't reach Gmail — nothing was sent."))
```

`handle()` — inside the `emails.*` block (before the `unknown request type` else):

```python
            elif type_ == "emails.send":
                ok, text = await self._send_email(payload)
                yield {"result": {"ok": ok, "message": text}}
            elif type_ == "emails.revise":
                try:
                    revised, err = await revise_email(
                        self._llm, str(payload.get("subject", "")),
                        str(payload.get("body", "")),
                        str(payload.get("instruction", "")))
                except LLMUnavailable as e:
                    yield {"error": str(e)}
                    return
                yield {"error": err} if revised is None else {"result": revised}
```

`handle()` — new top-level branch beside `confirm.response`:

```python
        elif type_ == "compose.response":
            # Resolves the chat turn awaiting this popup with its final fields
            # (or a cancel). If the id already expired but the user clicked
            # Send, send anyway — a Send click is never silently dropped.
            try:
                compose_id = int(payload["compose_id"])
            except (KeyError, TypeError, ValueError):
                yield {"error": "compose.response needs {compose_id}"}
                return
            fields = payload.get("fields")
            sending = bool(payload.get("send")) and isinstance(fields, dict)
            resolved = (self._confirm is not None
                        and self._confirm.resolve(compose_id,
                                                  fields if sending else False))
            if not resolved and sending and self._mail is not None:
                ok, text = await self._send_email(fields)
                yield {"result": {"ok": ok, "message": text}}
            else:
                yield {"result": {"ok": True, "message": ""}}
```

- [ ] **Step 4: run** `uv run pytest tests/daemon -q` — all PASS (broker/coercion regressions included).
- [ ] **Step 5: commit** `Route compose-shaped chat into a draft-filled popup`

### Task 5: UI plumbing — DaemonClient event + AppState compose surface

**Files:**
- Modify: `lumen/ui/daemon_client.py`, `lumen/ui_v2/state.py`, `lumen/ui_v2/app.py`
- Test: `tests/ui/test_daemon_client.py`, `tests/ui/test_ui_v2.py`

**Interfaces:**
- Consumes: Task 4 IPC shapes.
- Produces: `DaemonClient.compose_requested = pyqtSignal(dict)`; `AppState.compose_requested = pyqtSignal(dict)`, `attach_compose_source(client)`, `open_compose(prefill=None)`, `send_email(fields, cb)`, `revise_email(fields, cb)`, `respond_compose(compose_id, fields|None, cb=None)`; `_norm_mail` rows gain `"from_addr"`. Mock `email_confirm`/`reply_confirm` deleted.

- [ ] **Step 1: failing tests** — daemon_client: feeding the socket buffer a `{"compose_request": {...}, "compose_id": 7}` line emits `compose_requested` with `compose_id` merged (copy the existing confirm_request test pattern). ui_v2:

```python
def test_norm_mail_carries_sender_address():
    row = {"id": "x", "sender": "Ada L <a@x.com>", "subject": "s", "snippet": "",
           "received_at": "2026-07-10T10:00:00+00:00", "is_read": True}
    assert state_mod._norm_mail(row)["from_addr"] == "a@x.com"
    row["sender"] = "bare@x.com"
    assert state_mod._norm_mail(row)["from_addr"] == "bare@x.com"


def test_appstate_compose_senders_route_to_daemon():
    data, confirm = FakeClient(), FakeClient()
    st = AppState(data=data, confirm=confirm)
    st.send_email({"to": ["a@x.com"]}, lambda r: None)
    assert data.requests[-1][0] == "emails.send"
    st.revise_email({"subject": "s"}, lambda r: None)
    assert data.requests[-1][0] == "emails.revise"
    st.respond_compose(7, {"to": []})
    t, p, _cb = confirm.requests[-1]
    assert t == "compose.response" and p["compose_id"] == 7 and p["send"] is True
    st.respond_compose(7, None)
    assert confirm.requests[-1][1]["send"] is False


def test_appstate_open_compose_and_attached_source_emit_signal(qtbot):
    chat = FakeClient()
    st = AppState(data=FakeClient(), chat=chat)
    got = []
    st.compose_requested.connect(got.append)
    st.open_compose({"subject": "s"})
    chat.compose_requested.emit({"compose_id": 3, "to": ["a@x.com"]})
    assert got[0]["subject"] == "s" and got[1]["compose_id"] == 3
```

(FakeClient in `test_ui_v2.py` gains `compose_requested = pyqtSignal(dict)`.)

- [ ] **Step 2: run** — FAIL.
- [ ] **Step 3: implement** — daemon_client: signal + `elif "compose_request" in msg:` branch (above `chunk`) mirroring confirm_request. state.py: `import re`; `_norm_mail` computes `from_addr` (`re.search(r"<([^<>\s]+@[^<>\s]+)>", sender)` else the bare value if it contains `@`, else `""`); new signal + methods:

```python
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
```

In `__init__` live branch: `self.attach_compose_source(chat)` when chat is not None, plus `self.attach_compose_source(data)`. In `app.py` next to `state.attach_confirm_source(overlay_chat)`: `state.attach_compose_source(overlay_chat)`. Delete `email_confirm` and `reply_confirm` (their tests too — grep `tests/` for both names).

- [ ] **Step 4: run** `uv run pytest tests/ui/test_daemon_client.py tests/ui/test_ui_v2.py -q` — PASS.
- [ ] **Step 5: commit** `Give the UI a compose event surface`

### Task 6: ComposeDialog overlay + window wiring

**Files:**
- Create: `lumen/ui_v2/compose.py`
- Modify: `lumen/ui_v2/main.py`
- Test: `tests/ui/test_ui_v2.py`

**Interfaces:**
- Consumes: Task 5 AppState surface.
- Produces: `ComposeDialog(parent, state)` with `.open(payload)` (payload keys `to/cc/bcc/subject/body/reply_to/compose_id`, all optional); LumenWindow attribute `compose` opened via `state.compose_requested`.

- [ ] **Step 1: failing tests**

```python
def _win_state():
    return AppState(data=FakeClient(), chat=FakeClient(), confirm=FakeClient())


def test_compose_dialog_prefills_and_sends_oneshot(qtbot):
    from lumen.ui_v2.compose import ComposeDialog
    data = FakeClient()
    st = AppState(data=data, confirm=FakeClient())
    host = QWidget()
    qtbot.addWidget(host)
    dlg = ComposeDialog(host, st)
    dlg.open({"to": ["a@x.com"], "subject": "Hi", "body": "B", "reply_to": "m9"})
    assert dlg.to_edit.text() == "a@x.com" and dlg.subject_edit.text() == "Hi"
    dlg.cc_edit.setText("c@x.com, d@x.com")
    dlg._send()
    t, p, cb = data.requests[-1]
    assert t == "emails.send" and p["cc"] == ["c@x.com", "d@x.com"]
    assert p["reply_to"] == "m9"
    cb({"ok": False, "message": "Couldn't reach Gmail — nothing was sent."})
    assert dlg.isVisible()                      # failure keeps the draft open
    cb2_args = data.requests[-1]                # resend after the error
    dlg._send()
    data.requests[-1][2]({"ok": True, "message": "Sent."})
    assert not dlg.isVisible()


def test_compose_dialog_chat_mode_resolves_broker_not_oneshot(qtbot):
    from lumen.ui_v2.compose import ComposeDialog
    data, confirm = FakeClient(), FakeClient()
    st = AppState(data=data, confirm=confirm)
    host = QWidget()
    qtbot.addWidget(host)
    dlg = ComposeDialog(host, st)
    dlg.open({"compose_id": 5, "to": ["a@x.com"], "body": "b"})
    dlg._send()
    assert not any(t == "emails.send" for t, _p, _cb in data.requests)
    assert confirm.requests[-1][0] == "compose.response"
    assert confirm.requests[-1][1]["send"] is True
    assert not dlg.isVisible()                  # outcome lands in the chat turn
    dlg.open({"compose_id": 6})
    dlg._cancel()
    assert confirm.requests[-1][1] == {"compose_id": 6, "send": False, "fields": {}}


def test_compose_dialog_revise_refreshes_draft(qtbot):
    from lumen.ui_v2.compose import ComposeDialog
    data = FakeClient()
    st = AppState(data=data, confirm=FakeClient())
    host = QWidget()
    qtbot.addWidget(host)
    dlg = ComposeDialog(host, st)
    dlg.open({"subject": "Long", "body": "Long body", "to": ["a@x.com"]})
    dlg.revise_edit.setText("shorter")
    dlg._revise()
    t, p, cb = data.requests[-1]
    assert t == "emails.revise" and p["instruction"] == "shorter"
    assert not dlg.revise_btn.isEnabled()       # busy while the model works
    cb({"subject": "Short", "body": "B."})
    assert dlg.subject_edit.text() == "Short" and dlg.body_edit.toPlainText() == "B."
    assert dlg.to_edit.text() == "a@x.com"      # recipients never touched
    assert dlg.revise_btn.isEnabled() and dlg.revise_edit.text() == ""


def test_window_opens_compose_on_state_signal(qtbot):
    from lumen.ui_v2.main import LumenWindow
    win = LumenWindow(_win_state())
    qtbot.addWidget(win)
    win.state.compose_requested.emit({"subject": "s"})
    assert win.compose.isVisible() and win.compose.subject_edit.text() == "s"
```

- [ ] **Step 2: run** — FAIL (no module `compose`).
- [ ] **Step 3: implement** `lumen/ui_v2/compose.py` — ConfirmOverlay's overlay pattern (dim paintEvent, centered 560px `cls="dialog"` card, esc handling) with:
  - header: ✉ icon + "Compose email" (or "Reply" when `reply_to`) + the same WRITE ACTION warning line;
  - rows of `QLineEdit`s `to_edit`, `cc_edit`, `bcc_edit`, `subject_edit` (11px dim labels, comma-separated addresses), `QTextEdit body_edit` (~180px min height, sans 13px);
  - revise row: `QLineEdit revise_edit` placeholder "Ask Lumen to revise — e.g. shorter, more formal" + `button("✦ Revise", "outline")` → `_revise()`; busy = disabled + text "revising…"; re-enabled in `_revised` callback **and** on any `state.status_requested` (a daemon error means the callback never fires);
  - `err_lab` (12px, `T.WARN`, hidden) above the footer for send failures;
  - footer: Cancel (`esc`) + `Send` CompositeButtons (no Enter-to-send — an accidental Return must not send);
  - `_fields()` splits address boxes on commas, strips blanks, returns `{to, cc, bcc, subject, body, reply_to}`;
  - `_send()`: chat mode (`compose_id is not None`) → `state.respond_compose(id, fields)` + hide; else `state.send_email(fields, cb)` — cb hides + toasts on ok, else shows `err_lab` and stays open;
  - `_cancel()`: chat mode responds `respond_compose(id, None)`; always hides;
  - `open(payload)` resets every field from the payload (missing keys → empty), clears revise/err, `setGeometry(parent.rect())`, show/raise/focus to `to_edit` (body when `to` prefilled).

  `main.py`: import, `self.compose = ComposeDialog(self, self.state)` after the toast; `self.state.compose_requested.connect(self._open_compose)`; `_open_compose` mirrors `_open_confirm` (surface window, `self.compose.open(payload)`); resizeEvent also re-geometries `self.compose` when visible.
- [ ] **Step 4: run** `uv run pytest tests/ui/test_ui_v2.py -q` — PASS.
- [ ] **Step 5: commit** `Add the compose overlay dialog`

### Task 7: Mail screen buttons + spec/skill close-out

**Files:**
- Modify: `lumen/ui_v2/screens/mail.py`, `.claude/skills/email-menu.md` (send section: as-built note), `.claude/skills/development-plan.md` (Phase 7 step 1: done marker)
- Test: `tests/ui/test_ui_v2.py`

**Interfaces:**
- Consumes: `state.open_compose(prefill)`, `_norm_mail`'s `from_addr`.
- Produces: Compose button (list header), Reply button prefill `{to: [from_addr], subject: "Re: …", reply_to: id}`.

- [ ] **Step 1: failing tests**

```python
def test_mail_screen_compose_button_opens_empty_popup(qtbot):
    st = AppState()
    got = []
    st.compose_requested.connect(got.append)
    sc = MailScreen(st)
    qtbot.addWidget(sc)
    sc.compose_btn.click()
    assert got == [{}]


def test_mail_screen_reply_prefills_sender_and_threading(qtbot):
    st = AppState()
    st.mails[0].update({"from_addr": "priya@x.com", "subj": "Budget"})
    st.selected_mail = st.mails[0]["id"]
    got = []
    st.compose_requested.connect(got.append)
    sc = MailScreen(st)
    qtbot.addWidget(sc)
    sc.reply_btn.click()
    assert got[0]["to"] == ["priya@x.com"]
    assert got[0]["subject"] == "Re: Budget"
    assert got[0]["reply_to"] == st.mails[0]["id"]
```

- [ ] **Step 2: run** — FAIL (`compose_btn` missing; Reply still wired to deleted mock).
- [ ] **Step 3: implement** — header `top_row` gains `self.compose_btn = button("＋ Compose", "primary", px=11)` (fixed height 26, before the refresh button) → `state.open_compose()`. `_populate_pane`'s reply button becomes `self.reply_btn`, clicked → `self._reply(m)`:

```python
    def _reply(self, m: dict):
        subj = m["subj"] if m["subj"].lower().startswith("re:") else f"Re: {m['subj']}"
        self.state.open_compose({
            "to": [m["from_addr"]] if m.get("from_addr") else [],
            "subject": subj, "reply_to": m["id"]})
```

  Skill close-outs: `email-menu.md` send bullet gains "(as built 2026-07-13: the editable compose popup is itself the confirmation — no second dialog)"; `development-plan.md` Phase 7 step 1 marked done with the same one-liner and "gmail.modify already authorizes send — no scope change was needed".
- [ ] **Step 4: run** `uv run pytest tests/ -q` — full suite PASS.
- [ ] **Step 5: commit** `Wire Compose and Reply to the real popup`

### Task 8: Live verification (manual gate)

- [ ] Daemon + UI up; Mail screen → Compose → send a test mail to the user's own address → arrives in Gmail, popup toasts "✓ Sent.", message appears in the mirror after next sync.
- [ ] Open a real message → Reply → send → threads under the original in Gmail's web UI.
- [ ] Chat: "send an email to <own address> saying this is a lumen test" → popup pre-filled, edit a word, Send → chat turn ends "Sent."; repeat and Cancel → "Cancelled — nothing was sent." and nothing in Gmail.
- [ ] In the popup: "Ask Lumen to revise → 'make it two sentences'" → subject/body refresh, recipients untouched.
- [ ] Chat meta-question "can you send emails?" → no invented "local mirror only" policy talk.
