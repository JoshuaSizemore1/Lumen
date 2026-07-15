from lumen.daemon.llm import memory


def test_load_missing_returns_none(tmp_path):
    assert memory.load(tmp_path / "nope.md", 4000) is None


def test_load_truncates(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("x" * 5000)
    assert len(memory.load(p, 4000)) == 4000


def test_parse_and_render_roundtrip():
    text = ("## Calendar\n- Never books before 9am. (last seen 2026-07-14)\n\n"
            "## Todos\n- Groups errands with #errands. (last seen 2026-07-10)\n")
    parsed = memory.parse(text)
    assert parsed["Calendar"] == ["- Never books before 9am. (last seen 2026-07-14)"]
    assert parsed["Todos"][0].startswith("- Groups errands")
    rendered = memory.render(parsed)
    assert "## Calendar" in rendered and "## Todos" in rendered
    assert rendered.index("## Calendar") < rendered.index("## Todos")


def test_render_omits_empty_sections():
    out = memory.render({"Calendar": ["- a"], "Email": []})
    assert "## Calendar" in out
    assert "## Email" not in out


def test_memory_context_frames(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("## Books\n- Likes Le Guin. (last seen 2026-07-12)\n")
    ctx = memory.memory_context(p, 4000)
    assert "Le Guin" in ctx
    assert "learned" in ctx.lower()   # framing present
    assert memory.memory_context(tmp_path / "gone.md", 4000) is None


def test_write_atomic_and_perms(tmp_path):
    import stat
    p = tmp_path / "memory.md"
    memory.write(p, "## Todos\n- a\n")
    assert p.read_text() == "## Todos\n- a\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
