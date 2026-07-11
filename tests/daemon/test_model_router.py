"""ModelRouter: escalation is config-gated (benchmark first), write-shaped, tools-only."""

from lumen.daemon.llm.model_router import ModelRouter


def test_no_escalation_configured_always_fast():
    r = ModelRouter("fast")
    assert r.pick_model("save this to /tmp/x.txt", needs_tools=True) == "fast"
    assert r.pick_model("who wrote Dune", needs_tools=True) == "fast"


def test_write_shaped_tool_task_escalates_when_configured():
    r = ModelRouter("fast", "big")
    assert r.pick_model("save this to /tmp/x.txt", needs_tools=True) == "big"
    assert r.pick_model("move draft.txt to final.txt", needs_tools=True) == "big"


def test_read_shaped_stays_fast_even_when_configured():
    r = ModelRouter("fast", "big")
    assert r.pick_model("what files are in my notes folder", needs_tools=True) == "fast"
    assert r.pick_model("who wrote Dune", needs_tools=True) == "fast"


def test_no_tools_needed_stays_fast():
    r = ModelRouter("fast", "big")
    assert r.pick_model("save this to /tmp/x.txt", needs_tools=False) == "fast"


def test_empty_message_stays_fast():
    r = ModelRouter("fast", "big")
    assert r.pick_model("", needs_tools=True) == "fast"
