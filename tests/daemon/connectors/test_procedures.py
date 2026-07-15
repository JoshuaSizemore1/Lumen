from lumen.daemon.config import MemoryConfig
from lumen.daemon.connectors.procedures import ProcedureStore, write_procedure


def store(tmp_path, **kw):
    return ProcedureStore(tmp_path / "procedures", MemoryConfig(**kw))


def seed(store, state, slug, name, triggers, steps, last_used="2026-07-14"):
    d = store._dir(state)
    d.mkdir(parents=True, exist_ok=True)
    write_procedure(d / f"{slug}.md", name, triggers, steps, last_used)


def test_list_proposed(tmp_path):
    s = store(tmp_path)
    seed(s, "proposed", "morning", "Morning routine",
         ["morning routine", "start my day"], ["Run the briefing", "List todos"])
    props = s.list_proposed()
    assert props[0]["name"] == "Morning routine"
    assert "morning routine" in props[0]["triggers"]


def test_approve_moves_to_active(tmp_path):
    s = store(tmp_path)
    seed(s, "proposed", "morning", "Morning", ["morning routine"], ["Brief"])
    assert s.approve("morning") is True
    assert s.list_proposed() == []
    assert s.list_active()[0]["slug"] == "morning"


def test_approve_respects_cap(tmp_path):
    s = store(tmp_path, max_active_procedures=1)
    seed(s, "active", "a", "A", ["a"], ["x"])
    seed(s, "proposed", "b", "B", ["b"], ["y"])
    assert s.approve("b") is False


def test_dismiss_and_remove(tmp_path):
    s = store(tmp_path)
    seed(s, "proposed", "p", "P", ["p"], ["x"])
    seed(s, "active", "a", "A", ["a"], ["y"])
    assert s.dismiss("p") is True and s.list_proposed() == []
    assert s.remove("a") is True and s.list_active() == []


def test_match_bumps_last_used(tmp_path):
    s = store(tmp_path)
    seed(s, "active", "morning", "Morning", ["morning routine"], ["Brief"],
         last_used="2026-01-01")
    hit = s.match("run my morning routine please")
    assert hit is not None and hit["slug"] == "morning"
    assert s.match("unrelated text") is None
    assert s.list_active()[0]["last_used"] != "2026-01-01"
