"""Static regressions on the text the model reads. No model, no network.

Tier 1 of the 2026-07-18 tool-accuracy work fixed three pieces of prompt text
that were talking the model out of tool calls it was perfectly able to make.
Prompt text has no type checker and no compiler; without these assertions the
next well-meaning docstring edit can silently reintroduce the same denial.

Two anti-patterns are banned outright in tool descriptions we own:

  1. Unauthorized-service framing — describing the tool as reaching a remote
     service in wording that reads as "an account I do not have". `list_events`
     said "the user's Google Calendar" and got refused 3 times in 4 on private
     topics; `search_email` says "the user's locally mirrored email" and was
     never refused once.
  2. Permission hedges — "use this only when…", which the model reads as
     licence to decline rather than as scoping.
"""

import re

import pytest

from lumen.daemon.router import IDENTITY, calendar_context, mail_context
from tests.eval import cases, surface

# "Use this only for/when…" and friends. Scoping a tool is fine; telling the
# model when *not* to reach for it hands it a reason to stop.
HEDGE = re.compile(
    r"\buse (?:this|it) only\b|\bonly (?:use|call) (?:this|it)\b"
    r"|\bdoesn'?t already cover\b|\bunless (?:you|the user)\b",
    re.IGNORECASE)

# Phrasings that license "I can't do that" as a final answer.
DENIAL_LICENCE = re.compile(
    r"\bsay so if asked\b|\bif you (?:can'?t|cannot) access\b"
    r"|\byou (?:do not|don'?t) have access\b|\bnot shown\b",
    re.IGNORECASE)


@pytest.mark.parametrize("name,desc", sorted(surface.owned_descriptions().items()))
def test_no_hedges_or_denial_licence_in_tool_descriptions(name, desc):
    assert not HEDGE.search(desc), (
        f"{name}: permission hedge — scope the tool by saying when it applies, "
        f"not when to withhold it")
    assert not DENIAL_LICENCE.search(desc), (
        f"{name}: description licenses a refusal as a final answer")


def test_list_events_routes_out_of_window_dates_to_itself():
    """The specific failure: a far-future dentist appointment became a dead end
    because the description scoped the tool to what context already covered."""
    desc = surface.owned_descriptions()["list_events"]
    assert "outside" in desc.lower() and "window" in desc.lower()
    assert re.search(r"\bnever\b.{0,40}\b(decline|refuse)\b", desc,
                     re.IGNORECASE | re.DOTALL), \
        "list_events must state plainly that lack-of-access is not a valid reason to decline"


def test_owned_descriptions_do_not_gate_on_topic():
    """No description may make a tool's availability depend on subject matter —
    that is the shape a private-topic refusal takes."""
    topical = re.compile(r"\b(?:medical|health|legal|financial|personal|private|"
                         r"sensitive|confidential)\b", re.IGNORECASE)
    for name, desc in surface.owned_descriptions().items():
        assert not topical.search(desc), f"{name}: description gates on topic"


def test_identity_does_not_license_refusals():
    assert not DENIAL_LICENCE.search(IDENTITY)
    # The positive commitments that make the tool loop work at all.
    assert "never tell the user you can't access something you have a tool for" in IDENTITY


# --- context builders: naming a tool you don't hold invites role-play --------

def test_calendar_context_names_gcal_tools_only_when_attached():
    from datetime import date, datetime, timezone
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    end = date(2026, 9, 8)
    attached = calendar_context([], now, end, has_tool=True)
    assert "list_events" in attached and "search_events" in attached
    bare = calendar_context([], now, end)
    assert "list_events" not in bare and "search_events" not in bare


def test_mail_context_names_search_email_only_when_attached():
    counts = {"total": 120, "unread": 3}
    assert "search_email" in mail_context([], counts, True, has_tool=True)
    assert "search_email" not in mail_context([], counts, True)


# --- the surface itself ------------------------------------------------------

def test_write_tools_are_never_offered_to_the_model():
    """create_event / delete_event stay daemon-callable behind the confirm
    dialog. The gate is mechanical; this proves the model never sees them."""
    offered = surface.tool_names()
    assert "create_event" not in offered and "delete_event" not in offered
    assert {"create_event", "delete_event"} <= set(surface.owned_descriptions())


def test_surface_matches_production_not_the_old_bench():
    """The bench graded four todo tools and a `lookup_book` that do not exist.
    Pin the real names so a future eval cannot drift back. Three todo tools DO
    exist as of 2026-07-19 (in-process, not MCP); `delete_todo` and
    `lookup_book` never did."""
    assert surface.tool_names() == {"list_events", "search_events", "search_email",
                                    "get_email", "search_books", "get_book",
                                    "add_todo", "complete_todo", "list_todos"}


def test_config_allowlist_matches_the_servers():
    """A tool can exist on the server and still never reach the model, because
    config.toml's per-server `tools` list withholds it — and vice versa, config
    can name a tool that was renamed away. Either drift makes this eval grade a
    surface production does not have, which is the exact defect this suite was
    built to remove. Adding a tool means adding it to config.toml too."""
    allow = surface.allowlists()
    for group, module in surface.LOCAL_SERVERS.items():
        declared = allow.get(group)
        if declared is None:
            continue                       # expose-all: nothing to diverge
        actual = {name for name, _, _ in surface._raw()[group]}
        assert declared == actual, (
            f"{group}: config.toml allowlist and the server disagree — "
            f"only in config: {sorted(declared - actual)}, "
            f"only on the server: {sorted(actual - declared)}")


def test_search_events_is_the_topic_lookup_path():
    """The residual private-topic refusals were topic lookups with no date:
    'when is the parent-teacher conference?'. list_events cannot answer that
    without scanning a wide range, so search_events must own it and must say
    so, and list_events must hand it over rather than inviting a wide scan."""
    owned = surface.owned_descriptions()
    search, listing = owned["search_events"], owned["list_events"]
    assert "do not know its date" in search
    assert re.search(r"\bnever\b.{0,40}\b(decline|refuse)\b", search,
                     re.IGNORECASE | re.DOTALL)
    assert "search_events" in listing, \
        "list_events must redirect dateless topic lookups to search_events"


def test_todo_tools_exist_and_forbid_writing_a_todo_file():
    """Todos were context-only until 2026-07-19, which is why todo-fixes #19
    happened: the request carried the filesystem group and nothing that could
    touch a todo, so the model wrote a file called TODO. add_todo must say, in
    the description the model actually reads, that a file is never the answer."""
    owned = surface.owned_descriptions()
    assert {"add_todo", "complete_todo", "list_todos"} <= set(owned)
    assert "delete_todo" not in owned      # deletion stays a UI action
    assert re.search(r"never write a todo to a file", owned["add_todo"],
                     re.IGNORECASE)


def test_todo_tools_ride_only_with_their_group():
    assert "add_todo" in surface.tool_names({"todos"})
    assert "add_todo" not in surface.tool_names({"mail"})


# --- routing: these never reach the model -----------------------------------

@pytest.mark.parametrize("message,expected", cases.ROUTING_CASES)
def test_todo_shaped_messages_route_before_the_llm(message, expected):
    """Mirrors the `_chat` elif ladder's order, which is load-bearing:
    ALREADY_DONE sits after MARK_DONE, and both sit ahead of TODO_HINT."""
    from lumen.daemon.router import ALREADY_DONE, MARK_DONE, TODO_ADD, TODO_HINT
    if TODO_ADD.match(message):
        actual = "TODO_ADD"
    elif MARK_DONE.match(message):
        actual = "MARK_DONE"
    elif ALREADY_DONE.match(message):
        actual = "ALREADY_DONE"
    elif TODO_HINT.search(message):
        actual = "context-only"
    else:
        actual = "chat"
    assert actual == expected, f"{message!r} routed to {actual}, expected {expected}"
