"""writing_style: apply-often half — rules file load, cap, prompt injection."""
from lumen.daemon.llm.writing_style import MAX_CHARS, load_rules, styled


def test_load_rules_missing_file_is_none(tmp_path):
    assert load_rules(tmp_path / "nope.md") is None


def test_load_rules_empty_file_is_none(tmp_path):
    p = tmp_path / "writing-style.md"
    p.write_text("   \n")
    assert load_rules(p) is None


def test_load_rules_reads_and_caps(tmp_path):
    p = tmp_path / "writing-style.md"
    p.write_text("x" * (MAX_CHARS + 500))
    got = load_rules(p)
    assert got == "x" * MAX_CHARS  # capped — the file can't blow the prompt


def test_styled_appends_rules_and_keeps_system(tmp_path):
    p = tmp_path / "writing-style.md"
    p.write_text("- Signs off Respectfully")
    got = styled("SYSTEM PROMPT", p)
    assert got.startswith("SYSTEM PROMPT")
    assert "Respectfully" in got and "request wins" in got


def test_styled_without_rules_is_untouched(tmp_path):
    assert styled("SYSTEM PROMPT", tmp_path / "nope.md") == "SYSTEM PROMPT"
