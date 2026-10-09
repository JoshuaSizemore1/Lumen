"""Live tool-selection eval against the configured local model (or Claude:
LUMEN_EVAL_BACKEND=claude [LUMEN_EVAL_MODEL=haiku|sonnet]). Opt-in.

    LUMEN_EVAL_LIVE=1 uv run pytest tests/eval/test_live_accuracy.py -v

Skipped by default: it loads a model into RAM, which the power/thermal
constraint says never to do casually, and it takes minutes rather than
milliseconds. The static half of this suite (test_tool_surface.py) runs always
and catches prompt-text regressions for free; this half catches the thing static
text cannot — what the model actually decides to do.

What is real here vs. synthetic, stated plainly because the last eval got this
wrong: the tool schemas and IDENTITY come from production via `surface`, and the
context blocks are built by the router's own `calendar_context` / `mail_context`.
The *rows* fed to those builders are synthetic — a test cannot depend on the
user's real inbox. So this measures prompt shape and model behavior, not
retrieval quality.

`tool_choice` forcing is unavailable on this stack, so nothing structurally
prevents a refusal. That makes PRIVATE_TOPIC_PROBES the only guard against a
prompt edit quietly making Lumen decline to look up a therapy appointment.

Flakiness, measured 2026-07-18: across ~70 private-topic probe executions the
refusal rate is ~3-4%, and every residual failure was the same probe ("when is
the parent-teacher conference?") — the least calendar-shaped of the set. Before
the Tier 1 fixes that probe refused 100% of the time.

Sampling temperature is left at whatever the daemon uses, deliberately —
pinning it low here would make the eval pass under conditions production never
runs in, which is the exact defect this suite was built to remove. So read one
stray failure as noise and re-run; the regression this guards against (a
docstring that talks the model out of tool calls) showed up as four of four
private-topic probes failing together, not as a wobble.
"""

import os
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from lumen.daemon.config import load_config
from lumen.daemon.llm.client import LLMUnavailable, OllamaClient
from lumen.daemon.router import IDENTITY, calendar_context, mail_context
from tests.eval import cases, surface

pytestmark = [
    pytest.mark.skipif(os.environ.get("LUMEN_EVAL_LIVE") != "1",
                       reason="live model eval — set LUMEN_EVAL_LIVE=1 to run"),
    # One event loop for the whole module, matching the module-scoped client
    # fixture: a supervised llama-server must outlive a single test case.
    pytest.mark.asyncio(loop_scope="module"),
]

NOW = datetime(2026, 7, 18, 14, 5).astimezone()
WINDOW_END = NOW.date() + timedelta(days=60)

# A believable near-term calendar. Deliberately contains nothing matching the
# private-topic probes: the probe must produce a *lookup*, and finding the
# answer pre-loaded in context would hide a refusal rather than expose it.
EVENTS = [
    {"title": "Standup", "start_at": f"{NOW.date()}T09:30:00+00:00",
     "end_at": f"{NOW.date()}T09:45:00+00:00", "all_day": False,
     "calendar_name": "Work", "location": "", "attendees": []},
    {"title": "Lunch with Priya", "start_at": f"{NOW.date() + timedelta(days=2)}T12:00:00+00:00",
     "end_at": f"{NOW.date() + timedelta(days=2)}T13:00:00+00:00", "all_day": False,
     "calendar_name": "Personal", "location": "Cafe", "attendees": []},
]
UNREAD = [{"received_at": f"{NOW.date()}T08:12:00", "sender": "Chris Alvarez",
           "subject": "Invoice for June"}]
COUNTS = {"total": 1840, "unread": 1}


def _system_message(groups: set[str]) -> str:
    """The same assembly order `Router._build_messages` uses."""
    context = [IDENTITY]
    if "gcal" in groups:
        context.append(calendar_context(EVENTS, NOW, WINDOW_END, has_tool=True))
    if "mail" in groups:
        context.append(mail_context(UNREAD, COUNTS, connected=True, has_tool=True))
    return "\n\n".join(context)


async def _first_decision(client, message: str, groups: set[str]) -> dict:
    """Run one turn and report the model's first move: a tool call, or prose."""
    messages = [{"role": "system", "content": _system_message(groups)},
                {"role": "user", "content": message}]

    async def executor(name, args):   # never reached: we stop at the first call
        return ""

    try:
        async for event in client.chat_with_tools(
                messages, surface.tool_schemas(groups), executor, max_iterations=1):
            if "tool_call" in event:
                return {"tool": event["tool_call"]["name"],
                        "args": event["tool_call"]["arguments"]}
            if "content" in event:
                return {"tool": None, "text": event["content"]}
    except LLMUnavailable as e:
        pytest.skip(f"model unavailable: {e}")
    return {"tool": None, "text": ""}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client():
    """The configured Ollama chat client — the same object the daemon builds,
    so the eval measures what production runs. Override the model without
    editing config.toml:

        LUMEN_EVAL_LIVE=1 LUMEN_EVAL_MODEL=qwen3:14b \\
        uv run pytest tests/eval/test_live_accuracy.py

    Module-scoped so the whole suite shares one warm model rather than paying
    a cold load per case.
    """
    cfg = load_config()
    if os.environ.get("LUMEN_EVAL_BACKEND") == "claude":
        # Claude mode (2026-09-24): LUMEN_EVAL_MODEL picks a [claude] models
        # key (haiku|sonnet). Same client class the daemon builds.
        from lumen.daemon.llm.claude_cli import ClaudeCliClient
        key = os.environ.get("LUMEN_EVAL_MODEL", cfg.claude.default_model)
        c = ClaudeCliClient(cfg.claude.models[key], cli_path=cfg.claude.cli_path,
                            timeout_seconds=cfg.claude.timeout_seconds,
                            max_concurrent=cfg.claude.max_concurrent)
        yield c
        return
    model = os.environ.get("LUMEN_EVAL_MODEL", cfg.model)
    c = OllamaClient(cfg.ollama_url, model, cfg.keep_alive, think=cfg.think)
    try:
        yield c
    finally:
        await c.aclose()


@pytest.mark.parametrize("message,groups,expected,predicate", cases.TOOL_CASES)
async def test_tool_selection(client, message, groups, expected, predicate):
    got = await _first_decision(client, message, groups)
    assert got["tool"] == expected, (
        f"{message!r} -> {got.get('tool') or 'no tool: ' + got.get('text', '')[:120]!r}")
    if predicate is not None:
        assert predicate(got["args"]), f"{message!r} bad args: {got['args']}"


@pytest.mark.parametrize("message,groups,expected", cases.PRIVATE_TOPIC_PROBES)
async def test_private_topics_never_refused(client, message, groups, expected):
    """A sensitive subject is not a reason to decline. These must look it up.

    `expected` is a set: where two tools are both defensible the probe accepts
    either, because the regression being guarded is the refusal, not the choice
    between two correct lookups."""
    got = await _first_decision(client, message, groups)
    assert got["tool"] is not None, (
        f"REFUSAL on a private topic — {message!r} answered without a tool call: "
        f"{got.get('text', '')[:200]!r}")
    assert got["tool"] in expected, (
        f"{message!r} -> {got['tool']}, expected one of {sorted(expected)}")


async def test_far_future_date_widens_the_window(client, capsys):
    """Tier 1.2's regression: a date past the context window must become a
    calendar tool call, not 'that isn't shown'.

    Only the tool call is asserted. Resolving "March next year" to 2027 is date
    arithmetic, not tool selection, and a 4B model gets it wrong perhaps half
    the time — gating on it would make this suite flaky at the exact place it
    needs to be trusted. The wrong-year behavior is recorded in cases.GAPS
    instead, and reported here so a run still surfaces it. search_events is the
    better answer precisely because it needs no year at all.
    """
    got = await _first_decision(
        client, "Is my dentist appointment still booked for March next year?", {"gcal"})
    assert got["tool"] in {"search_events", "list_events"}, (
        f"window edge became a dead end: {got.get('text', '')[:200]!r}")
    if got["tool"] == "list_events":
        span = f"{got['args'].get('start')}{got['args'].get('end')}"
        if "2027" not in span:
            with capsys.disabled():
                print(f"\n  note: relative year unresolved — asked about March "
                      f"next year (2027), called with {got['args']}")
