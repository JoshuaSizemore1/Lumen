"""File authoring: sentinel-line parse + mechanical validation gate."""

from lumen.daemon.llm.file_write import parse_file, propose_file, validate_file


class FakeLLM:
    def __init__(self, text):
        self._text = text
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        yield self._text


def test_parse_minimal():
    got = parse_file("FILENAME: notes.md\n---\n# Hi\n\nbody")
    assert got == {"filename": "notes.md", "path": None, "content": "# Hi\n\nbody"}


def test_parse_with_path_and_preamble():
    text = ("Sure, here you go:\n"
            "FILENAME: plan.md\n"
            "PATH: ~/Documents/Plans\n"
            "---\n"
            "line one\nline two")
    got = parse_file(text)
    assert got["filename"] == "plan.md"
    assert got["path"] == "~/Documents/Plans"
    assert got["content"] == "line one\nline two"


def test_parse_unwraps_one_code_fence():
    got = parse_file("FILENAME: x.md\n---\n```markdown\n# T\nbody\n```")
    assert got["content"] == "# T\nbody"


def test_parse_needs_filename_and_separator():
    assert parse_file("---\njust a body, no filename") is None
    assert parse_file("FILENAME: x.md\nno separator line") is None
    assert parse_file("") is None


def test_parse_ignores_path_line_after_separator():
    # a "PATH:" that appears inside the body is content, not a header
    got = parse_file("FILENAME: x.md\n---\nPATH: not a header\nbody")
    assert got["path"] is None
    assert got["content"] == "PATH: not a header\nbody"


def test_validate_round_trips_and_strips_quotes():
    got = validate_file({"filename": ' "note.md" ', "content": " hello ",
                         "path": " ~/Docs "})
    assert got == {"filename": "note.md", "path": "~/Docs", "content": "hello"}


def test_validate_appends_md_when_no_suffix():
    got = validate_file({"filename": "summary", "content": "x"})
    assert got["filename"] == "summary.md"


def test_validate_rejects_traversal_and_folders():
    assert validate_file({"filename": "../escape.md", "content": "x"}) is None
    assert validate_file({"filename": "a/b.md", "content": "x"}) is None
    assert validate_file({"filename": r"a\b.md", "content": "x"}) is None
    assert validate_file({"filename": ".hidden", "content": "x"}) is None


def test_validate_rejects_empty_content_and_overlong_name():
    assert validate_file({"filename": "ok.md", "content": "   "}) is None
    assert validate_file({"filename": "x" * 81 + ".md", "content": "y"}) is None
    assert validate_file(None) is None
    assert validate_file("nope") is None


async def test_propose_file_parses():
    llm = FakeLLM("FILENAME: recap.md\n---\n# Recap\n\n- one\n- two")
    prop, err = await propose_file(llm, "write a markdown recap file")
    assert err is None
    assert prop == {"filename": "recap.md", "path": None,
                    "content": "# Recap\n\n- one\n- two"}


async def test_propose_file_honest_failure_on_garbage():
    prop, err = await propose_file(FakeLLM("I can't do that"), "write a file")
    assert prop is None and "couldn't turn that into a file" in err
