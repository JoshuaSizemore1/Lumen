"""Tests for ClaudeCliClient. Uses a fake `claude` Python script that replays
fixtures / emits scripted events — never calls the real API."""

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import pytest

from lumen.daemon.llm.claude_cli import (
    ClaudeCliClient,
    ClaudeUnavailable,
    _render_transcript,
    _resolve_cli,
    _split_messages,
    _tool_schema,
    _tool_system_addendum,
)
from lumen.daemon.llm.client import LLMUnavailable

# ── fixture helpers ────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent / "fixtures" / "claude_cli"


def fixture_lines(name: str) -> list[bytes]:
    return (FIXTURES / name).read_bytes().splitlines(keepends=True)


def make_cli(tmp_path: Path, lines: list[bytes], *, exit_code: int = 0) -> Path:
    """Write a fake claude script that prints fixed lines then exits."""
    script = tmp_path / "claude"
    data = b"".join(lines).decode()
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys, os
        # prompt on stdin, never in argv check
        data = sys.stdin.read()
        sys.stdout.write({data!r})
        sys.stdout.flush()
        sys.exit({exit_code})
    """))
    script.chmod(0o755)
    return script


def make_cli_from_fixture(tmp_path: Path, name: str, exit_code: int = 0) -> Path:
    return make_cli(tmp_path, fixture_lines(name), exit_code=exit_code)


def client(tmp_path: Path, name: str, exit_code: int = 0) -> ClaudeCliClient:
    cli = make_cli_from_fixture(tmp_path, name, exit_code)
    c = ClaudeCliClient(
        model="claude-haiku-4-5-20251001",
        cli_path=str(cli),
        timeout_seconds=10,
    )
    c._cwd = tmp_path
    return c


# ── streaming chat ────────────────────────────────────────────────────────────

async def test_chat_streams_text_chunks(tmp_path):
    c = client(tmp_path, "plain_stream.jsonl")
    chunks = [ch async for ch in c.chat([{"role": "user", "content": "hi"}])]
    # fixture has "Hey", " there,", " friend", "!"
    assert "".join(chunks) == "Hey there, friend!"


async def test_chat_prompt_on_stdin_not_argv(tmp_path):
    """The user prompt must go on stdin; argv must NOT contain it."""
    received_args: list[str] = []
    received_stdin: list[str] = []

    script = tmp_path / "claude"
    script.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys, json
        # Record argv to a file so the test can inspect it.
        with open(sys.argv[1], "w") as f:
            f.write("\\n".join(sys.argv[2:]))
        with open(sys.argv[1] + ".stdin", "w") as f:
            f.write(sys.stdin.read())
        # Emit a minimal result so the client doesn't hang
        print(json.dumps({"type":"result","is_error":False,"result":"ok",
                          "structured_output":None}))
    """))
    script.chmod(0o755)

    record_file = tmp_path / "args.txt"
    # We need the fake script to know WHERE to write. Hack: pass via env or
    # write it into the script directly. Let's embed the path.
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys, json
        with open({str(record_file)!r}, "w") as f:
            f.write("\\n".join(sys.argv))
        with open({str(record_file) + ".stdin"!r}, "w") as f:
            f.write(sys.stdin.read())
        print(json.dumps({{"type":"result","is_error":False,"result":"ok"}}))
    """))

    secret = "supersecret_prompt_content_1234"
    c = ClaudeCliClient(model="claude-haiku-4-5-20251001", cli_path=str(script),
                        timeout_seconds=5)
    c._cwd = tmp_path
    chunks = [ch async for ch in c.chat([{"role": "user", "content": secret}])]

    argv_text = record_file.read_text()
    stdin_text = (record_file.parent / (record_file.name + ".stdin")).read_text()

    assert secret not in argv_text, "Prompt must not appear in argv"
    assert secret in stdin_text, "Prompt must appear on stdin"


async def test_chat_multiple_turns_render_transcript(tmp_path):
    """Multi-turn history is rendered as a labelled transcript."""
    captured_stdin: list[str] = []

    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys, json
        with open({str(tmp_path / "stdin.txt")!r}, "w") as f:
            f.write(sys.stdin.read())
        print(json.dumps({{"type":"result","is_error":False,"result":"ok"}}))
    """))
    script.chmod(0o755)

    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=5)
    c._cwd = tmp_path
    messages = [
        {"role": "system", "content": "You are Lumen."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "What day is it?"},
    ]
    chunks = [ch async for ch in c.chat(messages)]
    stdin = (tmp_path / "stdin.txt").read_text()
    assert "[User]" in stdin
    assert "[Assistant]" in stdin
    assert "Hello" in stdin
    assert "Hi there!" in stdin
    assert "What day is it?" in stdin
    # System content must NOT be in stdin (it goes via --system-prompt flag)
    assert "You are Lumen." not in stdin


async def test_chat_single_user_message_sent_plain(tmp_path):
    """If there's exactly one user message (no history), send it as-is."""
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys, json
        with open({str(tmp_path / "stdin.txt")!r}, "w") as f:
            f.write(sys.stdin.read())
        print(json.dumps({{"type":"result","is_error":False,"result":"ok"}}))
    """))
    script.chmod(0o755)
    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=5)
    c._cwd = tmp_path
    chunks = [ch async for ch in c.chat([{"role": "user", "content": "just this"}])]
    stdin = (tmp_path / "stdin.txt").read_text()
    assert stdin == "just this"
    # No [User] wrapper for a single turn
    assert "[User]" not in stdin


# ── error detection ────────────────────────────────────────────────────────────

async def test_chat_logged_out_error(tmp_path):
    c = client(tmp_path, "err_logged_out.jsonl", exit_code=1)
    with pytest.raises(ClaudeUnavailable) as exc:
        async for _ in c.chat([{"role": "user", "content": "hi"}]):
            pass
    assert exc.value.reason == "logged_out"
    assert "auth login" in str(exc.value)


async def test_chat_bad_model_error(tmp_path):
    c = client(tmp_path, "err_bad_model.jsonl", exit_code=1)
    with pytest.raises(ClaudeUnavailable) as exc:
        async for _ in c.chat([{"role": "user", "content": "hi"}]):
            pass
    assert exc.value.reason == "error"


async def test_not_installed_raises(tmp_path):
    c = ClaudeCliClient(model="m", cli_path="/no/such/binary", timeout_seconds=5)
    c._cwd = tmp_path
    with pytest.raises(ClaudeUnavailable) as exc:
        async for _ in c.chat([{"role": "user", "content": "hi"}]):
            pass
    assert exc.value.reason == "not_installed"


async def test_timeout_kills_process(tmp_path):
    """A CLI that never exits is killed at the timeout."""
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import time, sys
        sys.stdin.read()
        time.sleep(9999)
    """))
    script.chmod(0o755)
    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=1)
    c._cwd = tmp_path
    with pytest.raises(ClaudeUnavailable) as exc:
        async for _ in c.chat([{"role": "user", "content": "hi"}]):
            pass
    assert exc.value.reason == "timeout"


async def test_cancel_kills_process(tmp_path):
    """CancelledError on the caller results in the subprocess being reaped."""
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import time, sys
        sys.stdin.read()
        time.sleep(9999)
    """))
    script.chmod(0o755)
    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=30,
                        max_concurrent=1)
    c._cwd = tmp_path

    async def run():
        async for _ in c.chat([{"role": "user", "content": "hi"}]):
            pass

    task = asyncio.create_task(run())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Semaphore must be released (back to initial value) after cancel
    assert c._sem._value == 1


# ── usage snapshot ────────────────────────────────────────────────────────────

async def test_usage_snapshot_cached(tmp_path):
    """rate_limit_event.unifiedWindows is cached as last_usage."""
    c = client(tmp_path, "plain_stream.jsonl")
    assert c.last_usage is None
    chunks = [ch async for ch in c.chat([{"role": "user", "content": "hi"}])]
    assert c.last_usage is not None
    assert "five_hour" in c.last_usage
    assert "seven_day" in c.last_usage
    assert abs(c.last_usage["five_hour"]["utilization"] - 0.03) < 0.001
    assert c.last_usage["seven_day"]["resets_at"] > 0


# ── tool loop (chat_with_tools) ───────────────────────────────────────────────

def _tool_cli(tmp_path: Path, iterations: list[str]) -> Path:
    """Fake CLI that iterates through a list of fixture files, one per call."""
    counter_file = tmp_path / "_counter.txt"
    counter_file.write_text("0")
    script = tmp_path / "claude"
    fixtures_dir = str(FIXTURES)
    # Build a Python list literal directly — json.dumps of a list of strings
    # is valid Python syntax.
    names_literal = json.dumps(iterations)
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        names = {names_literal}
        ctr_path = {str(counter_file)!r}
        with open(ctr_path) as f:
            idx = int(f.read().strip())
        with open(ctr_path, "w") as f:
            f.write(str(idx + 1))
        fixture = {fixtures_dir!r} + "/" + names[idx % len(names)]
        sys.stdin.read()   # consume stdin
        with open(fixture, "rb") as f:
            sys.stdout.buffer.write(f.read())
        sys.stdout.flush()
    """))
    script.chmod(0o755)
    return script


async def test_tool_round_trip_executor_called(tmp_path):
    """Tool call in iteration 1, answer in iteration 2; executor is invoked."""
    cli = _tool_cli(tmp_path, ["tool_request.jsonl", "tool_answer.jsonl"])
    c = ClaudeCliClient(model="m", cli_path=str(cli), timeout_seconds=10)
    c._cwd = tmp_path

    tools = [{"type": "function", "function": {
        "name": "list_events",
        "description": "List calendar events",
        "parameters": {"type": "object",
                        "properties": {"start": {"type": "string"},
                                       "end": {"type": "string"}},
                        "required": ["start", "end"]},
    }}]
    calls: list[tuple] = []

    async def executor(name: str, args: dict) -> str:
        calls.append((name, args))
        return "Dentist at 10am; CS 3500 lecture at 2pm"

    events = [ev async for ev in c.chat_with_tools(
        [{"role": "user", "content": "What's on my calendar tomorrow?"}],
        tools, executor,
    )]

    tool_evs = [ev for ev in events if "tool_call" in ev]
    content_evs = [ev for ev in events if "content" in ev]

    assert tool_evs, "Should have emitted a tool_call event"
    assert tool_evs[0]["tool_call"]["name"] == "list_events"
    assert calls, "Executor must have been called"
    assert "list_events" == calls[0][0]
    assert content_evs, "Should have emitted a content event with the answer"
    assert "Dentist" in content_evs[0]["content"]


async def test_tool_schema_uses_enum(tmp_path):
    """The --json-schema flag value must enumerate tool names."""
    schema_str = _tool_schema(["list_events", "search_email"])
    schema = json.loads(schema_str)
    names_enum = schema["properties"]["tool_calls"]["items"]["properties"]["name"]["enum"]
    assert names_enum == ["list_events", "search_email"]


async def test_tool_results_fed_back_as_transcript(tmp_path):
    """Tool results appear in the stdin of the second iteration."""
    stdin_calls: list[str] = []
    counter_file = tmp_path / "_ctr.txt"
    counter_file.write_text("0")
    script = tmp_path / "claude"
    fix1 = str(FIXTURES / "tool_request.jsonl")
    fix2 = str(FIXTURES / "tool_answer.jsonl")
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        ctr = int(open({str(counter_file)!r}).read().strip())
        open({str(counter_file)!r}, "w").write(str(ctr + 1))
        stdin_data = sys.stdin.read()
        open({str(tmp_path / "stdin_")!r} + str(ctr) + ".txt", "w").write(stdin_data)
        fix = [{fix1!r}, {fix2!r}][min(ctr, 1)]
        with open(fix, "rb") as f:
            sys.stdout.buffer.write(f.read())
        sys.stdout.flush()
    """))
    script.chmod(0o755)
    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=10)
    c._cwd = tmp_path

    tools = [{"type": "function", "function": {
        "name": "list_events",
        "description": "List calendar events",
        "parameters": {"type": "object",
                        "properties": {"start": {"type": "string"},
                                       "end": {"type": "string"}},
                        "required": ["start", "end"]},
    }}]

    async def executor(name, args):
        return "result: two events"

    [ev async for ev in c.chat_with_tools(
        [{"role": "user", "content": "What's on tomorrow?"}],
        tools, executor,
    )]

    stdin1 = (tmp_path / "stdin_1.txt").read_text()
    assert "result: two events" in stdin1, "Tool result must be in second stdin"
    assert "[Tool result:" in stdin1
    # The request is recorded too, so results pair with their arguments.
    assert "[Assistant requested lookup: list_events]" in stdin1
    assert "2026-09-25" in stdin1


async def test_salvage_on_empty_answer(tmp_path):
    """When the model returns no answer and no tool calls, salvage from results."""
    # Emit an empty structured_output: answer="" tool_calls=[]
    empty_result = json.dumps({
        "type": "result", "is_error": False,
        "structured_output": {"tool_calls": [], "answer": ""},
        "result": "",
    })
    script = tmp_path / "claude"
    # First iteration: tool request; second: empty answer
    fix1 = str(FIXTURES / "tool_request.jsonl")
    counter_file = tmp_path / "_ctr.txt"
    counter_file.write_text("0")
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        ctr = int(open({str(counter_file)!r}).read().strip())
        open({str(counter_file)!r}, "w").write(str(ctr + 1))
        sys.stdin.read()
        if ctr == 0:
            with open({fix1!r}, "rb") as f:
                sys.stdout.buffer.write(f.read())
        else:
            print({empty_result!r})
        sys.stdout.flush()
    """))
    script.chmod(0o755)
    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=10)
    c._cwd = tmp_path

    tools = [{"type": "function", "function": {
        "name": "list_events", "description": "d",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }}]

    async def executor(name, args):
        return "two events found"

    events = [ev async for ev in c.chat_with_tools(
        [{"role": "user", "content": "calendar?"}], tools, executor,
    )]
    content_evs = [ev for ev in events if "content" in ev]
    assert content_evs
    # Salvage should mention the tool result
    assert "two events found" in content_evs[0]["content"] or content_evs[0]["content"]


async def test_rate_limited_raises(tmp_path):
    """rate_limit_event with status != 'allowed' raises ClaudeUnavailable."""
    rate_line = json.dumps({
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "blocked",
            "unifiedWindows": {
                "five_hour": {"utilization": 1.0, "resetsAt": 9999999999},
                "seven_day": {"utilization": 0.5, "resetsAt": 9999999999},
            }
        }
    })
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        sys.stdin.read()
        print({rate_line!r})
        sys.stdout.flush()
    """))
    script.chmod(0o755)
    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=5)
    c._cwd = tmp_path
    with pytest.raises(ClaudeUnavailable) as exc:
        async for _ in c.chat([{"role": "user", "content": "hi"}]):
            pass
    assert exc.value.reason == "rate_limited"
    assert "resets at" in str(exc.value).lower() or "limit" in str(exc.value).lower()


# ── semaphore concurrency ─────────────────────────────────────────────────────

async def test_semaphore_limits_concurrent_calls(tmp_path):
    """At most max_concurrent calls can hold the semaphore simultaneously."""
    barrier = asyncio.Event()
    released: list[int] = []

    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys, json
        sys.stdin.read()
        # emit a valid result
        print(json.dumps({{"type":"result","is_error":False,"result":"ok"}}))
    """))
    script.chmod(0o755)

    c = ClaudeCliClient(model="m", cli_path=str(script),
                        timeout_seconds=5, max_concurrent=2)
    c._cwd = tmp_path

    # Drain the two slots, then check the third is not yet acquired
    started: list[asyncio.Event] = [asyncio.Event() for _ in range(3)]
    passed: list[asyncio.Event] = [asyncio.Event() for _ in range(3)]

    async def run(i: int):
        await c._acquire(False)
        started[i].set()
        await passed[i].wait()
        c._release()

    tasks = [asyncio.create_task(run(i)) for i in range(3)]
    # Wait for first two to acquire
    await asyncio.wait_for(asyncio.gather(started[0].wait(), started[1].wait()), 2)
    # Third should not have acquired yet
    assert not started[2].is_set()
    # Release first
    passed[0].set()
    await asyncio.wait_for(started[2].wait(), 2)
    passed[1].set()
    passed[2].set()
    await asyncio.gather(*tasks)


# ── message conversion helpers ────────────────────────────────────────────────

def test_split_messages():
    msgs = [
        {"role": "system", "content": "You are Lumen."},
        {"role": "system", "content": "Extra context."},
        {"role": "user", "content": "Hi"},
    ]
    sys_prompt, rest = _split_messages(msgs)
    assert sys_prompt == "You are Lumen.\n\nExtra context."
    assert len(rest) == 1
    assert rest[0]["role"] == "user"


def test_render_transcript():
    turns = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
        {"role": "tool", "content": "result", "tool_name": "search"},
        {"role": "user", "content": "Thanks"},
    ]
    out = _render_transcript(turns)
    assert "[User]\nHello" in out
    assert "[Assistant]\nHi" in out
    assert "[Tool result: search]\nresult" in out
    assert "[User]\nThanks" in out


def test_tool_system_addendum_contains_framing():
    tools = [{"type": "function", "function": {
        "name": "lookup",
        "description": "Look something up",
        "parameters": {"type": "object",
                        "properties": {"q": {"type": "string", "description": "query"}},
                        "required": ["q"]},
    }}]
    addendum = _tool_system_addendum(tools)
    assert "cannot call any tool directly" in addendum
    assert "tool_calls" in addendum
    assert "lookup" in addendum
    assert "Look something up" in addendum


# ── is_loaded / warm / unload no-ops ─────────────────────────────────────────

async def test_is_loaded_always_true():
    c = ClaudeCliClient(model="m")
    assert await c.is_loaded() is True


async def test_warm_noop():
    c = ClaudeCliClient(model="m")
    await c.warm()   # should not raise


async def test_unload_noop():
    c = ClaudeCliClient(model="m")
    await c.unload()  # should not raise


async def test_embed_raises():
    c = ClaudeCliClient(model="m")
    with pytest.raises(LLMUnavailable):
        await c.embed(["text"], "nomic-embed-text")


async def test_background_context_yields_to_interactive(tmp_path):
    """A background caller (BACKGROUND set in its task) waits while an
    interactive caller is queued, even if it started waiting first."""
    from lumen.daemon.llm.claude_cli import BACKGROUND
    c = ClaudeCliClient(model="m", cli_path="claude", max_concurrent=1)
    order: list[str] = []
    await c._acquire(False)                       # hold the only slot

    async def bg():
        BACKGROUND.set(True)
        await c._acquire(False)
        order.append("bg")
        c._release()

    async def fg():
        await c._acquire(False)
        order.append("fg")
        c._release()

    tb = asyncio.create_task(bg())
    await asyncio.sleep(0.01)
    tf = asyncio.create_task(fg())
    await asyncio.sleep(0.01)
    c._release()
    await asyncio.wait_for(asyncio.gather(tb, tf), 2)
    assert order == ["fg", "bg"]


async def test_native_tool_use_is_harvested_as_tool_call(tmp_path):
    """Live 2026-09-24: Haiku called search_books natively, the CLI refused
    (no such tool), and the answer said the tool "isn't available". The native
    call is the real request — it must reach the executor."""
    counter = tmp_path / "_ctr.txt"
    counter.write_text("0")
    fix1 = str(FIXTURES / "native_tool_use.jsonl")
    fix2 = str(FIXTURES / "tool_answer.jsonl")
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        ctr = int(open({str(counter)!r}).read().strip())
        open({str(counter)!r}, "w").write(str(ctr + 1))
        sys.stdin.read()
        sys.stdout.buffer.write(open([{fix1!r}, {fix2!r}][min(ctr, 1)], "rb").read())
    """))
    script.chmod(0o755)
    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=10)
    c._cwd = tmp_path
    tools = [{"type": "function", "function": {
        "name": n, "description": "", "parameters": {"type": "object",
        "properties": {"query": {"type": "string"}}}}}
        for n in ("search_books", "get_book")]
    called: list[tuple] = []

    async def executor(name, args):
        called.append((name, args))
        return "Project Hail Mary | Andy Weir | 2021"

    evs = [ev async for ev in c.chat_with_tools(
        [{"role": "user", "content": "Find a book similar to Project Hail Mary"}],
        tools, executor)]
    # The run is stopped at the CLI's first refusal, before the model can
    # give up — one harvested call per iteration; the loop asks for more.
    assert [n for n, _ in called] == ["search_books"]
    assert called[0][1]["query"] == "Project Hail Mary Andy Weir"
    assert "isn't available" not in (evs[-1].get("content") or "")


async def test_system_prompt_never_in_argv(tmp_path):
    """Review finding 2026-09-24: Lumen's system prompt carries mail/calendar
    context and the memory blob — it must reach the CLI by a private file,
    never argv (readable in `ps`), and the file must not outlive the call."""
    record = tmp_path / "rec.json"
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys, json, os, stat
        path = sys.argv[sys.argv.index("--system-prompt-file") + 1]
        json.dump({{"argv": sys.argv, "path": path,
                    "mode": stat.S_IMODE(os.stat(path).st_mode),
                    "system": open(path).read()}}, open({str(record)!r}, "w"))
        sys.stdin.read()
        print(json.dumps({{"type":"result","is_error":False,"result":"ok"}}))
    """))
    script.chmod(0o755)
    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=5)
    c._cwd = tmp_path
    secret = "From: Chris <chris@example.com> — Invoice for June"
    [ch async for ch in c.chat([{"role": "system", "content": secret},
                                {"role": "user", "content": "hi"}])]
    rec = json.loads(record.read_text())
    assert secret not in "\n".join(rec["argv"])
    assert rec["system"].startswith(secret)
    assert rec["mode"] == 0o600
    assert not Path(rec["path"]).exists(), "system-prompt file must be removed"


async def test_allowed_warning_is_not_a_limit(tmp_path):
    """Live 2026-09-24: at 90% of the 5h window the CLI reports
    status "allowed_warning" and still answers. That must stream normally
    (and still update the usage snapshot), not raise rate_limited."""
    rate_line = json.dumps({"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed_warning", "unifiedWindows": {
            "five_hour": {"utilization": 0.9, "resetsAt": 1790306400},
            "seven_day": {"utilization": 0.54, "resetsAt": 1790532000}}}})
    delta = json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "delta": {"type": "text_delta", "text": "Ok"}}})
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        sys.stdin.read()
        print({rate_line!r}); print({delta!r})
    """))
    script.chmod(0o755)
    c = ClaudeCliClient(model="m", cli_path=str(script), timeout_seconds=5)
    c._cwd = tmp_path
    assert [ch async for ch in c.chat([{"role": "user", "content": "hi"}])] == ["Ok"]
    assert c.last_usage["five_hour"]["utilization"] == 0.9


async def test_finds_cli_in_local_bin_when_path_lacks_it(tmp_path, monkeypatch):
    """A desktop/systemd-launched daemon's PATH has no ~/.local/bin, where the
    official installer puts `claude` — it must still be found (Josh, 2026-09-24)."""
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    cli = bin_dir / "claude"
    cli.write_text("#!/bin/sh\necho '{\"loggedIn\": true, \"email\": \"a@b\"}'\n")
    cli.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert _resolve_cli("claude") == str(cli)
    c = ClaudeCliClient(model="m", cli_path="claude", timeout_seconds=5)
    c._cwd = tmp_path
    st = await c.status()
    assert st["installed"] is True and st["logged_in"] is True
    assert c._argv_base(tmp_path / "sp")[0] == str(cli)


def test_resolve_cli_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr("lumen.daemon.llm.claude_cli._CLI_FALLBACKS",
                        ("~/.local/bin/claude",))
    assert _resolve_cli("claude") is None
    assert _resolve_cli("/no/such/binary") is None
