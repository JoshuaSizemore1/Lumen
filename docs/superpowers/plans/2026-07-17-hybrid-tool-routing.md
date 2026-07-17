# Hybrid Tool Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach LLM tools by subject-matter groups (union), add an LLM classifier fallback for regex misses, and make it structurally impossible for a prompt to name a tool the model doesn't hold.

**Architecture:** Three seams change: `MCPBridge.ollama_tools()` learns to filter by owning server; `Router` replaces the narrow verb-regex tool gate with subject-group routing plus a one-shot classifier fallback (new `daemon/llm/intent.py`); `mail_context`/`fs_context` injection becomes conditional on the matching tool group actually being attached. Spec: `docs/superpowers/specs/2026-07-17-hybrid-tool-routing-design.md`.

**Tech Stack:** Python 3.12, asyncio, pytest (async tests run under the repo's existing pytest-asyncio auto mode — plain `async def test_*` functions, no decorator).

## Global Constraints

- Run tests with `uv run pytest <path> -q` from the repo root `/home/josh/Projects/Lumen`.
- No new dependencies. No second LLM instance — the classifier reuses the resident model via `llm.chat`.
- Every commit message ends with the prompts line and has NO Co-Authored-By trailer. The plan-doc commit (already made) carried this batch's `This commit used 1 prompt.`; every implementation commit in the batch ends `This commit used 0 prompts.` (one user prompt drove the whole implementation).
- Comments: terse, explain constraints only, match the codebase's existing density/voice.
- `WRITE_TOOLS` filtering, `WriteGate`, `ConfirmBroker`, `max_iterations`, and all specialized pipeline routes keep their exact current behavior.

---

### Task 1: Server-filtered `ollama_tools`

**Files:**
- Modify: `lumen/daemon/llm/mcp_bridge.py` (MCPBridge.ollama_tools ~line 73, LazyBridge.ollama_tools ~line 182)
- Test: `tests/daemon/llm/test_mcp_bridge.py`

**Interfaces:**
- Produces: `MCPBridge.ollama_tools(servers: set[str] | None = None) -> list[dict]` — `None` = all tools (unchanged behavior); a set = only tools whose *owning server name* is in the set (resolved via `_registry`, NOT name prefixes — names are only prefixed on collision). `LazyBridge.ollama_tools(servers=None)` passes through, `[]` before start. Task 4 relies on exactly this signature.

- [ ] **Step 1: Write the failing tests** (append to `tests/daemon/llm/test_mcp_bridge.py` after `test_ollama_tools_filtered_by_allowlist`)

```python
async def test_ollama_tools_filtered_by_server():
    fs = FakeClient([T("read_file"), T("list_directory")])
    mail = FakeClient([T("search_email"), T("get_email")])
    bridge = MCPBridge({"fs": fs, "mail": mail}, {"fs": None, "mail": None})
    await bridge.load_tools()
    names = {t["function"]["name"] for t in bridge.ollama_tools(servers={"mail"})}
    assert names == {"search_email", "get_email"}
    assert len(bridge.ollama_tools()) == 4                 # None keeps everything
    assert bridge.ollama_tools(servers=set()) == []


async def test_server_filter_uses_registry_not_name_prefix():
    # Names are only namespaced on collision — filtering must resolve the
    # owning server through the registry, never by splitting the name.
    a, b = FakeClient([T("search")]), FakeClient([T("search")])
    bridge = MCPBridge({"fs": a, "books": b}, {"fs": None, "books": None})
    await bridge.load_tools()
    names = {t["function"]["name"] for t in bridge.ollama_tools(servers={"books"})}
    assert names == {"books__search"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/daemon/llm/test_mcp_bridge.py -q -k server`
Expected: FAIL — `TypeError: ollama_tools() got an unexpected keyword argument 'servers'`

- [ ] **Step 3: Implement**

In `MCPBridge`, replace `ollama_tools`:

```python
    def ollama_tools(self, servers: set | None = None) -> list[dict]:
        """All tool schemas, or only those owned by the named servers. Owner
        comes from the registry — exposed names are only prefixed on collision,
        so prefix-matching would be wrong."""
        if servers is None:
            return list(self._schemas)
        return [s for s in self._schemas
                if self._registry[s["function"]["name"]][0] in servers]
```

In `LazyBridge`, replace `ollama_tools`:

```python
    def ollama_tools(self, servers: set | None = None) -> list[dict]:
        return self._bridge.ollama_tools(servers) if self._bridge else []
```

- [ ] **Step 4: Run the bridge suite**

Run: `uv run pytest tests/daemon/llm/test_mcp_bridge.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/mcp_bridge.py tests/daemon/llm/test_mcp_bridge.py
git commit -m "feat: server-filtered ollama_tools on the MCP bridge

Groundwork for subject-group tool routing: the router will ask for tool
schemas by owning server. Registry-resolved, since names are only
namespaced on collision.

This commit used 0 prompts."
```

---

### Task 2: Intent classifier module

**Files:**
- Create: `lumen/daemon/llm/intent.py`
- Test: `tests/daemon/llm/test_intent.py` (new file)

**Interfaces:**
- Produces: `async def classify(llm, message: str) -> set[str]` in `lumen.daemon.llm.intent`, returning a subset of `intent.LABELS` (`{"email", "calendar", "files", "todos", "books", "send_email", "create_event"}`). Empty set on NONE, garbage, or `LLMUnavailable`. Task 5 imports both names.

- [ ] **Step 1: Write the failing tests** — full contents of `tests/daemon/llm/test_intent.py`:

```python
from lumen.daemon.llm import intent
from lumen.daemon.llm.client import LLMUnavailable


class FakeLLM:
    def __init__(self, reply="NONE", fail=False):
        self._reply, self._fail = reply, fail
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        if self._fail:
            raise LLMUnavailable("down")
        yield self._reply


async def test_single_label():
    assert await intent.classify(FakeLLM("email"), "any emals from Ada?") == {"email"}


async def test_multi_label_with_spacing_and_case():
    got = await intent.classify(FakeLLM(" Email, CALENDAR "), "mail about tmrw?")
    assert got == {"email", "calendar"}


async def test_none_and_garbage_yield_empty():
    assert await intent.classify(FakeLLM("NONE"), "hello!") == set()
    assert await intent.classify(FakeLLM("well, it depends…"), "hello!") == set()


async def test_unknown_labels_dropped_known_kept():
    assert await intent.classify(FakeLLM("email, weather"), "x") == {"email"}


async def test_llm_down_degrades_to_empty():
    assert await intent.classify(FakeLLM(fail=True), "x") == set()


async def test_prompt_carries_message_and_label_menu():
    llm = FakeLLM("NONE")
    await intent.classify(llm, "any emals from Ada?")
    assert llm.messages[-1]["content"] == "any emals from Ada?"
    system = llm.messages[0]["content"]
    for label in intent.LABELS:
        assert label in system
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/daemon/llm/test_intent.py -q`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` on `lumen.daemon.llm.intent`

- [ ] **Step 3: Implement** — full contents of `lumen/daemon/llm/intent.py`:

```python
"""Regex-miss fallback for chat routing: one small strict-format call on the
resident fast model naming which subsystems a message involves. Any parse
failure or LLM error degrades to 'no labels' — the caller falls back to plain
chat, exactly the pre-classifier behavior."""

from lumen.daemon.llm.client import LLMUnavailable

LABELS = frozenset({"email", "calendar", "files", "todos", "books",
                    "send_email", "create_event"})

_SYSTEM = (
    "Classify what the user's message involves. Reply with ONLY a "
    "comma-separated subset of these labels, or NONE:\n"
    "email — reading or searching their email/inbox\n"
    "send_email — writing, sending, or replying to an email\n"
    "calendar — their schedule, events, meetings, or availability\n"
    "create_event — booking or scheduling something new\n"
    "todos — their tasks or todo list\n"
    "books — books they own, have read, or want recommended\n"
    "files — files or folders on their computer\n"
    "NONE — general conversation needing none of the above\n"
    "Misspellings still count. No explanation."
)


async def classify(llm, message: str) -> set[str]:
    try:
        text = ""
        async for chunk in llm.chat([{"role": "system", "content": _SYSTEM},
                                     {"role": "user", "content": message}]):
            text += chunk
    except LLMUnavailable:
        return set()
    return {part for part in (p.strip().lower() for p in text.split(","))
            if part in LABELS}
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/daemon/llm/test_intent.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/intent.py tests/daemon/llm/test_intent.py
git commit -m "feat: one-shot intent classifier for chat routing fallback

This commit used 0 prompts."
```

---

### Task 3: Honest `mail_context`

**Files:**
- Modify: `lumen/daemon/router.py` — `mail_context` (~line 336)
- Test: `tests/daemon/test_router.py` (~line 2126 area)

**Interfaces:**
- Produces: `mail_context(unread, counts, connected, syncing=False, brief=False, has_tool=False)` — `has_tool=True` means `search_email` is attached to this same request and only then may the text name it. Task 4's `_build_messages` passes `has_tool="mail" in groups`.

- [ ] **Step 1: Write the failing test** (add near `test_mail_context_lines_and_markers`)

```python
def test_mail_context_without_tool_never_names_search_email():
    # Honesty rule (live fabrication 2026-07-17): a model told about a tool it
    # doesn't hold role-plays using it. No tool attached → no tool mentioned.
    unread = [{"received_at": "2026-07-12T09:30:00", "sender": "Ada <a@x.com>",
               "subject": "Engines"}]
    for kwargs in ({"brief": False}, {"brief": True}):
        ctx = mail_context(unread, {"total": 40, "unread": 1}, True, **kwargs)
        assert "search_email" not in ctx
    with_tool = mail_context(unread, {"total": 40, "unread": 1}, True,
                             has_tool=True)
    assert "search_email" in with_tool
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/daemon/test_router.py -q -k without_tool_never_names`
Expected: FAIL — `TypeError: mail_context() got an unexpected keyword argument 'has_tool'`

- [ ] **Step 3: Implement** — replace `mail_context` in `lumen/daemon/router.py`:

```python
def mail_context(unread: list[dict], counts: dict, connected: bool,
                 syncing: bool = False, brief: bool = False,
                 has_tool: bool = False) -> str:
    """System-message context: unread summary from the local mirror, explicit
    empty/not-connected/still-syncing markers. `brief` drops the enumerated
    unread rows and keeps only the counts — used when mail context rides along
    on a (non-mail-shaped) tool loop purely as grounding. `has_tool` marks that
    search_email is attached to THIS request; only then may the text name it —
    a model told about a tool it doesn't hold role-plays using it (live
    fabrication 2026-07-17)."""
    if not connected:
        return ("Gmail is not connected yet — the user needs to run the one-time "
                "Google setup. Say so if asked about email; do not invent messages.")
    if brief:
        head = (f"The user's mailbox mirror holds {counts['total']} messages, "
                f"{counts['unread']} unread"
                + (" — use the search_email tool to read any of them."
                   if has_tool else "."))
        return (head if not syncing else
                "The first mailbox sync has not finished — the mirror is "
                "incomplete; missing messages are not absent, just not pulled "
                "yet.\n" + head)
    lines = [f"The user's mailbox mirror holds {counts['total']} messages, "
             f"{counts['unread']} unread. Unread messages "
             + ("(only these are shown — use the search_email tool for "
                "anything else):" if has_tool
                else "(only these are shown in this conversation):")]
    if syncing:
        lines.insert(0, "The first mailbox sync has not finished — the mirror is "
                        "incomplete. Say so if asked about email; missing "
                        "messages are not absent, just not pulled yet.")
    if not unread:
        lines.append("No unread messages.")
    for m in unread:
        when = m["received_at"][:16].replace("T", " ")
        lines.append(f"- {when}: {m['sender']} — {m['subject']}")
    return "\n".join(lines)
```

- [ ] **Step 4: Fix the two existing direct-call tests.** `test_mail_context_lines_and_markers` and `test_mail_context_flags_incomplete_first_sync` (~lines 2126–2151) call `mail_context(...)` directly; if either asserts the `search_email` pointer, add `has_tool=True` to that call. Do not weaken their other assertions.

- [ ] **Step 5: Run the mail-context tests**

Run: `uv run pytest tests/daemon/test_router.py -q -k mail_context`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add lumen/daemon/router.py tests/daemon/test_router.py
git commit -m "feat: mail_context only names search_email when it is attached

This commit used 0 prompts."
```

---

### Task 4: Subject-group routing fast path

**Files:**
- Modify: `lumen/daemon/router.py` — module docstring, new constants after `WRITE_TOOLS` (~line 249), `_build_messages` (~439), `_messages_for` (~483), delete `_tool_shaped` (~494), `_chat` routing tail (~1009–1032), `_chat_with_tools` (~1727), `_plain_chat` (~1103)
- Test: `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: Task 1's `ollama_tools(servers=...)`, Task 3's `has_tool` kwarg.
- Produces (Task 5 relies on these exact names):
  - `SERVER_GROUPS = frozenset({"fs", "mail", "gcal", "books"})` (module constant)
  - `Router._subject_groups(message: str) -> set[str]`
  - `Router._engaged_groups(conv_id) -> set[str]`
  - `Router._group_subsystem(groups) -> str` (static)
  - `Router._chat_with_tools(message, conv_id=None, groups: frozenset[str])`
  - `Router._plain_chat(message, conv_id, groups: frozenset[str] = frozenset())`
  - `Router._messages_for(message, conv_id, groups: frozenset[str] = frozenset())`

- [ ] **Step 1: Write the failing regression test** (add after `test_chat_non_lookup_skips_tools_even_with_bridge`)

```python
class CaptureToolsLLM:
    """Records which tool schemas the router attached, answers directly."""
    def __init__(self):
        self.tools = None

    async def chat_with_tools(self, messages, tools, executor, *, model=None,
                              max_iterations=4):
        self.tools = tools
        yield {"content": "checked."}


async def test_verbless_mail_question_gets_mail_tools():
    # Regression for the 2026-07-17 fabrication: "what emails were sent to me
    # on the 8th this month" has no read-verb, matched no tool route, and took
    # the plain path — whose mail context named search_email. The model
    # role-played the search and invented three emails. Subject-shaped mail
    # questions must enter the tool loop with the mail tools attached.
    bridge = FakeBridge(tools=(("search_email", "mail"), ("get_email", "mail"),
                               ("list_directory", "fs")))
    llm = CaptureToolsLLM()
    router = Router(llm, FakeStore(), mail=FakeMailSync(),
                    mail_store=FakeMailStore(), bridge=bridge,
                    model_router=FakeModelRouter())
    out = await collect(router, "chat",
                        {"message": "what emails were sent to me on the 8th this month"})
    names = {t["function"]["name"] for t in llm.tools}
    assert {"search_email", "get_email"} <= names
    assert "list_directory" not in names        # no fs hint → no fs tools
    assert {"done": True} in out


async def test_multi_subject_message_gets_group_union():
    bridge = FakeBridge(tools=(("search_email", "mail"), ("list_events", "gcal"),
                               ("list_directory", "fs")))
    llm = CaptureToolsLLM()
    router = Router(llm, FakeStore(), calendar=FakeCalendar(),
                    mail=FakeMailSync(), mail_store=FakeMailStore(),
                    bridge=bridge, model_router=FakeModelRouter())
    out = await collect(router, "chat",
                        {"message": "any emails about tomorrow's schedule"})
    names = {t["function"]["name"] for t in llm.tools}
    assert {"search_email", "list_events"} <= names
    assert "list_directory" not in names
```

Note: `FakeCalendar` already exists in this test file if any calendar chat test defines one — search for `class FakeCalendar` first; if it does not exist at module scope, hoist the one defined inside the calendar-context tests (it needs only `list_range(a, b) -> []`, `connected = True`, `last_sync()`, `window()`), or add:

```python
class FakeCalendar:
    connected = True

    def list_range(self, frm, to):
        return []

    def last_sync(self):
        return "2026-07-12T13:00:00"

    def window(self):
        return ("2026-06-12", "2026-08-12")
```

- [ ] **Step 2: Update `FakeBridge` for server-tagged tools** (test file, ~line 422). The second tuple element was an ignored placeholder; it becomes the owning server name. Replace the class:

```python
class FakeBridge:
    def __init__(self, tools=(("list_directory", "fs"),), result="a.txt\nb.txt",
                 fail=False):
        self._tools = list(tools)               # (exposed_name, server_name)
        self._result = result
        self._fail = fail
        self.started = False
        self.calls = []

    async def ensure_started(self):
        self.started = True

    def ollama_tools(self, servers=None):
        return [{"type": "function", "function": {"name": n}}
                for n, server in self._tools
                if servers is None or server in servers]

    async def call(self, name, args):
        self.calls.append((name, args))
        if self._fail:
            raise ToolCallError("boom")
        return self._result
```

Then update every `FakeBridge(tools=...)` call site that passed `{}` placeholders to pass a server name instead — `grep -n "FakeBridge(tools=" tests/daemon/test_router.py` and use `"fs"` for filesystem tools, `"gcal"` for `create_event`/`delete_event`/`list_events`, `"mail"` for mail tools, `"books"` for book tools. Also grep the whole test file for any other fake defining `def ollama_tools(self)` (e.g. slow-call or transport-error fixtures) and give each the `servers=None` parameter, returning its tools unfiltered.

- [ ] **Step 3: Run to verify the new tests fail**

Run: `uv run pytest tests/daemon/test_router.py -q -k "verbless or group_union"`
Expected: FAIL — mail question takes the plain path (`llm.tools` is None / AttributeError on chat)

- [ ] **Step 4: Implement the router changes.**

4a. Module docstring (line 4): replace `"the regex hints below — cheap heuristics, no LLM pre-pass."` with `"subject hints below plus a small-model classifier fallback on total regex miss (daemon/llm/intent.py)."` (The fallback itself lands in Task 5 — the docstring describes the finished design; both tasks commit together only if you prefer, otherwise this line is one commit early. Keep it here: one docstring edit, not two.)

4b. After the `WRITE_TOOLS` block (~line 249) add:

```python
# Tool groups = MCP server names. Tools attach by subject-matter group (union
# on multi-subject messages) keyed off the same wide hints that inject context
# — the old split (wide hints for context, narrow verb regexes for tools) let
# a message get context naming search_email with no tools attached, and the
# model role-played the search (live fabrication 2026-07-17). Liberal bias is
# deliberate: a false positive costs a few schema tokens, a miss costs a
# fabricated answer. "todos" is a context-only pseudo-group (no MCP server).
SERVER_GROUPS = frozenset({"fs", "mail", "gcal", "books"})
```

4c. Replace `_tool_shaped` (~line 494) with:

```python
    def _subject_groups(self, message: str) -> set[str]:
        """Which tool groups this message's subject matter wants — the same
        wide hints that key context injection, never narrower verb shapes."""
        groups = set()
        if self._mail_store is not None and MAIL_HINT.search(message):
            groups.add("mail")
        if self._calendar is not None and CAL_HINT.search(message):
            groups.add("gcal")
        if self._books is not None and BOOK_HINT.search(message):
            groups.add("books")
        if TOOL_HINT.search(message) or FS_WRITE_HINT.search(message):
            groups.add("fs")
        return groups

    def _engaged_groups(self, conv_id: int | None) -> set[str]:
        """A tool-engaged conversation keeps every group on a bare follow-up
        ('and delete it') — same all-tools behavior as before groups existed."""
        if (self._conv is not None and conv_id is not None
                and self._conv.is_tool_engaged(conv_id)):
            return set(SERVER_GROUPS)
        return set()

    @staticmethod
    def _group_subsystem(groups) -> str:
        """Memory-log subsystem name for a group set. fs outranks mail because
        mail rides along on every tool loop as grounding."""
        for group, name in (("fs", "files"), ("gcal", "calendar"),
                            ("books", "books"), ("mail", "email"),
                            ("todos", "todos")):
            if group in groups:
                return name
        return "chat"
```

4d. In `_chat` (~lines 1009–1032):

Replace the `MAIL_READ_HINT` branch body:

```python
        elif (self._bridge is not None and self._mail_store is not None
                and MAIL_READ_HINT.search(message)):
            # Read-shaped mail request: the tool loop owns inbox search/QA.
            sub = self._chat_with_tools(
                message, conv_id,
                groups=frozenset(self._subject_groups(message) | {"mail"}))
            subsystem = "email"
```

Replace the two-line tail:

```python
        elif self._bridge is not None and (groups := (
                self._subject_groups(message) or self._engaged_groups(conv_id))):
            if self._mail_store is not None:
                groups.add("mail")   # ride-along: two small schemas, and every
                                     # tool loop can be asked a mail follow-up
            sub = self._chat_with_tools(message, conv_id,
                                        groups=frozenset(groups))
            subsystem = self._group_subsystem(groups)
        else:
            sub, subsystem = self._plain_chat(message, conv_id), self._infer_subsystem(message)
```

4e. `_build_messages` — replace the `tool_loop: bool = False` parameter with `groups: frozenset[str] = frozenset()` (= the groups whose tools are actually attached; empty on the plain path and the bridge-down fallback). New body for the context-injection section (identity/memory/procedures lines stay exactly as they are):

```python
        if TODO_HINT.search(message) or "todos" in groups:
            context.append(todo_context(self._todos.open_todos(), date.today()))
        if self._books is not None and (BOOK_HINT.search(message)
                                        or "books" in groups):
            context.append(self._books.catalog_context())
        if self._calendar is not None and (CAL_HINT.search(message)
                                           or "gcal" in groups):
            now = datetime.now().astimezone()
            end = now.date() + timedelta(days=CAL_CONTEXT_DAYS)
            context.append(calendar_context(
                self._calendar.list_range(now.date().isoformat(), end.isoformat()),
                now, end))
        mail_shaped = bool(MAIL_HINT.search(message))
        if self._mail_store is not None and (mail_shaped or "mail" in groups):
            # Non-mail-shaped tool loops still get the counts as grounding;
            # the search_email pointer only rides when the tool itself does.
            context.append(mail_context(
                self._mail_store.unread(limit=10), self._mail_store.counts(),
                self._mail.connected if self._mail is not None else False,
                syncing=self._mail.syncing if self._mail is not None else False,
                brief=not mail_shaped, has_tool="mail" in groups))
        if self._bridge is not None and "fs" in groups:
            context.append(fs_context(Path.home()))
```

Update the docstring: context is keyed on the current message's subject hints OR an attached group (the classifier can attach groups the regexes missed); grounding that names tools rides only when its group is attached.

4f. `_messages_for` — same parameter swap, passed through:

```python
    def _messages_for(self, message: str, conv_id: int | None,
                      groups: frozenset[str] = frozenset()) -> list[dict]:
```

(body unchanged apart from `self._build_messages(message, history, groups)`).

4g. `_plain_chat` gains a pass-through (Task 5's context-only routing needs it):

```python
    async def _plain_chat(self, message: str, conv_id: int | None,
                          groups: frozenset[str] = frozenset()):
        messages = self._messages_for(message, conv_id, groups)
```

(rest of the body unchanged).

4h. `_chat_with_tools` — new signature and tool selection; fallback goes honest (no groups → no tool-referencing grounding):

```python
    async def _chat_with_tools(self, message: str, conv_id: int | None = None,
                               groups: frozenset[str] = SERVER_GROUPS):
        try:
            await self._bridge.ensure_started()
            tools = [t for t in self._bridge.ollama_tools(
                         servers=set(groups) & set(SERVER_GROUPS))
                     if t.get("function", {}).get("name", "").split("__")[-1]
                     not in WRITE_TOOLS]
        except Exception:
            log.exception("MCP bridge unavailable — answering without tools")
            tools = []
        if not tools:   # no servers came up → plain chat, honest context only
            messages = self._messages_for(message, conv_id)
```

(the rest of the fallback branch and the pump machinery stay byte-identical; the later `messages = self._messages_for(message, conv_id, tool_loop=True)` line becomes `messages = self._messages_for(message, conv_id, groups)`).

- [ ] **Step 5: Run the new tests, then the full router suite**

Run: `uv run pytest tests/daemon/test_router.py -q -k "verbless or group_union"` → PASS
Run: `uv run pytest tests/daemon/test_router.py -q`
Expected: failures only in tests encoding the OLD behavior. Fix each per the new rules — known candidates:
  - Fixtures with `def ollama_tools(self)` missed in Step 2 → add `servers=None`.
  - `test_chat_mail_context_carries_sync_state` (~2154): a mail-shaped chat now runs the tool loop when a bridge is present; without a bridge it stays plain and its context must NOT contain `search_email`. Adjust its assertion to the syncing marker text, not the tool pointer.
  - Any test asserting fs grounding on the empty-tools fallback: the fallback is now honest (no `fs_context`) — assert its absence instead.
  - Tests routing calendar/book-shaped chit-chat to the plain path while constructing a Router WITH a bridge (e.g. a calendar-context test that also passes `bridge=`): these now enter the tool loop; give the fake LLM a `chat_with_tools` or drop the bridge from the construction, whichever the test's intent is (context assertions → drop the bridge).
Every fix must preserve the test's original intent; none may delete an assertion about confirm gating or write tools.

- [ ] **Step 6: Run the whole daemon suite**

Run: `uv run pytest tests/daemon -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add lumen/daemon/router.py tests/daemon/test_router.py
git commit -m "feat: attach tools by subject group, not verb regex

Subject hints (the wide ones that already inject context) now key tool
attachment too, unioned across subjects, with mail riding along on every
tool loop. Fixes the 2026-07-17 fabrication: a verbless mail question took
the plain path whose context named search_email — the 4B role-played the
search. Grounding that names a tool now rides only with the tool itself.

This commit used 0 prompts."
```

---

### Task 5: Classifier fallback wiring

**Files:**
- Modify: `lumen/daemon/router.py` — import block, `_chat` tail (the `else:` branch from Task 4d)
- Test: `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: `intent.classify` / `intent.LABELS` (Task 2), `SERVER_GROUPS`, `_group_subsystem`, `_chat_with_tools(groups=)`, `_plain_chat(groups=)` (Task 4).

- [ ] **Step 1: Write the failing tests**

```python
class RoutingLLM:
    """chat() answers as the intent classifier; chat_with_tools records
    what the router attached."""
    def __init__(self, verdict="NONE"):
        self._verdict = verdict
        self.tools = None
        self.chat_calls = 0

    async def chat(self, messages):
        self.chat_calls += 1
        yield self._verdict

    async def chat_with_tools(self, messages, tools, executor, *, model=None,
                              max_iterations=4):
        self.tools = tools
        yield {"content": "ok"}


async def test_regex_miss_classifier_attaches_labeled_group():
    # "emals" matches no hint anywhere — the classifier fallback must land
    # the mail tools instead of a blind plain chat.
    llm = RoutingLLM(verdict="email")
    router = Router(llm, FakeStore(), mail=FakeMailSync(),
                    mail_store=FakeMailStore(),
                    bridge=FakeBridge(tools=(("search_email", "mail"),)),
                    model_router=FakeModelRouter())
    await collect(router, "chat", {"message": "any emals from Ada?"})
    assert {t["function"]["name"] for t in llm.tools} == {"search_email"}


async def test_classifier_none_stays_plain_and_streams():
    llm = RoutingLLM(verdict="NONE")
    router = Router(llm, FakeStore(),
                    bridge=FakeBridge(tools=(("search_email", "mail"),)),
                    model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "good morning!"})
    assert llm.tools is None                      # never entered the tool loop
    assert llm.chat_calls == 2                    # classify, then the answer
    assert out[-1] == {"done": True}


async def test_no_bridge_skips_classifier():
    llm = RoutingLLM(verdict="email")
    router = Router(llm, FakeStore())
    await collect(router, "chat", {"message": "any emals from Ada?"})
    assert llm.chat_calls == 1                    # just the plain answer


async def test_classifier_send_email_routes_to_compose(monkeypatch):
    llm = RoutingLLM(verdict="send_email")
    router = Router(llm, FakeStore(), mail=FakeMailSync(),
                    mail_store=FakeMailStore(), bridge=FakeBridge(),
                    confirm=object(), model_router=FakeModelRouter())

    async def fake_compose(message):
        yield {"chunk": "compose opened"}
        yield {"done": True}

    monkeypatch.setattr(router, "_compose_email_chat", fake_compose)
    out = await collect(router, "chat", {"message": "shoot sam a thanks note"})
    assert {"chunk": "compose opened"} in out


async def test_classifier_labels_for_absent_subsystems_fall_to_plain():
    # calendar label but no calendar wired: degrade to plain chat, no crash.
    llm = RoutingLLM(verdict="calendar")
    router = Router(llm, FakeStore(),
                    bridge=FakeBridge(tools=(("search_email", "mail"),)),
                    model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "am I fre tmrw evening"})
    assert llm.tools is None
    assert out[-1] == {"done": True}
```

Note on messages: every message above must genuinely miss all hints — verify with a quick REPL check against the imported regexes before trusting a test ("good morning!", "any emals from Ada?", "shoot sam a thanks note", "am I fre tmrw evening" were chosen to miss `MAIL_HINT`/`CAL_HINT`/`COMPOSE_HINT`/`TOOL_HINT`; if one matches after all, pick a different misspelling and leave a comment saying which hint it must miss).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/daemon/test_router.py -q -k classifier`
Expected: FAIL — no fallback exists; misses go straight to plain chat (`chat_calls == 1`, `tools is None`)

- [ ] **Step 3: Implement.** Add `from lumen.daemon.llm import intent` to the router's import block (the `from lumen.daemon.llm import (...)` group). Replace the final `else:` of `_chat`'s routing chain (Task 4d) with:

```python
        else:
            sub, subsystem = None, self._infer_subsystem(message)
            if self._bridge is not None:
                # Total regex miss with tools available: one small classifier
                # call on the resident model beats a blind plain chat that
                # could get coached into role-playing a lookup.
                labels = await intent.classify(self._llm, message)
                if ("send_email" in labels and self._confirm is not None
                        and self._mail is not None):
                    sub, subsystem = self._compose_email_chat(message), "email"
                elif ("create_event" in labels and self._confirm is not None
                        and self._calendar is not None):
                    sub, subsystem = self._create_event_chat(message), "calendar"
                else:
                    available = {
                        "mail": self._mail_store is not None,
                        "gcal": self._calendar is not None,
                        "books": self._books is not None,
                        "fs": True, "todos": True,
                    }
                    label_groups = {"email": "mail", "calendar": "gcal",
                                    "files": "fs", "todos": "todos",
                                    "books": "books"}
                    groups = {label_groups[l] for l in labels
                              if l in label_groups and available[label_groups[l]]}
                    if groups & SERVER_GROUPS:
                        if self._mail_store is not None:
                            groups.add("mail")
                        sub = self._chat_with_tools(message, conv_id,
                                                    groups=frozenset(groups))
                        subsystem = self._group_subsystem(groups)
                    elif groups:   # todos-only: context ride, no tools
                        sub = self._plain_chat(message, conv_id,
                                               groups=frozenset(groups))
                        subsystem = "todos"
            if sub is None:
                sub = self._plain_chat(message, conv_id)
```

- [ ] **Step 4: Run the classifier tests, then both suites**

Run: `uv run pytest tests/daemon/test_router.py -q -k classifier` → PASS
Run: `uv run pytest tests/daemon -q`
Expected: all PASS. Pre-existing chit-chat tests that construct a bridge-less Router are untouched by design (`test_no_bridge_skips_classifier` pins that); a chit-chat test WITH a bridge now makes two `chat` calls — if one asserts on `llm.messages`, remember `FakeLLM.messages` holds the LAST call (the plain answer), which is the right one, so assertions should still hold.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/router.py tests/daemon/test_router.py
git commit -m "feat: intent-classifier fallback for regex-miss chat routing

A message matching no hint at all now gets one small classification call
on the resident model — labels map to tool groups or the compose/event
pipelines (both still confirm-gated); NONE or an unreachable model falls
back to plain chat exactly as before.

This commit used 0 prompts."
```

---

### Task 6: Full verification + docs

**Files:**
- Modify: `.claude/skills/mcp-integration.md` (routing note), `docs/superpowers/specs/2026-07-17-hybrid-tool-routing-design.md` (status line)

- [ ] **Step 1: Run everything**

Run: `uv run pytest tests/ -q`
Expected: all PASS, no skips introduced by this work

- [ ] **Step 2: Update `mcp-integration.md`.** Append to the "Bridging local models to MCP" section:

```markdown
**Routing (2026-07-17):** tools attach by subject-matter group (= MCP server
name), keyed off the same wide hints that inject context, unioned across
subjects, mail riding along on every tool loop; a total regex miss runs one
small classification call on the resident model (`daemon/llm/intent.py`)
instead of a blind plain chat. Context that names a tool only rides when that
tool is attached — the 2026-07-17 fabrication came from violating exactly
that. Full design: docs/superpowers/specs/2026-07-17-hybrid-tool-routing-design.md.
```

- [ ] **Step 3: Update the spec status line** from `**Status:** Approved (user, 2026-07-17)` to `**Status:** Implemented 2026-07-17`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/mcp-integration.md docs/superpowers/specs/2026-07-17-hybrid-tool-routing-design.md
git commit -m "docs: record hybrid tool routing in mcp-integration skill

This commit used 0 prompts."
```

---

## Self-Review Notes

- Spec coverage: §1 subject groups → Task 4; §2 classifier fallback → Tasks 2+5; §3 honesty rule → Tasks 3+4 (fs grounding gated on the fs group; `fs_context` names `list_directory`); §4 multi-step unchanged → no task (constraint only); §5 error handling → Task 2 (LLMUnavailable→∅) and Task 4h (bridge-down fallback); testing section → regression + union + classifier + honesty tests above.
- Type consistency: `groups` is `frozenset[str]` at every boundary (`set` only as local scratch); label names match `intent.LABELS` everywhere; `ollama_tools(servers=set)` matches Task 1's signature.
- Live verification (after Task 6, optional but recommended): use the `verify` skill to send the screenshot phrasing over the daemon socket and confirm a `tool_used: search_email` event appears.
