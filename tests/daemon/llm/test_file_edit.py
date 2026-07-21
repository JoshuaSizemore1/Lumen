from lumen.daemon.llm.file_edit import MAX_EDIT_CHARS, parse_reply, propose_edit


class FakeLLM:
    def __init__(self, chunks):
        self._chunks = chunks
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        for c in self._chunks:
            yield c


def test_parse_reply_sentinel_format():
    changes, content = parse_reply(
        "CHANGES: fix 'flashlite' to 'flashlight'\n---\n# List\n- flashlight")
    assert changes == "fix 'flashlite' to 'flashlight'"
    assert content == "# List\n- flashlight"


def test_parse_reply_keeps_content_dashes():
    # markdown horizontal rules / frontmatter --- inside the FILE must survive
    _c, content = parse_reply("CHANGES: x\n---\ntop\n---\nbottom")
    assert content == "top\n---\nbottom"


def test_parse_reply_tolerates_format_misses():
    # no header at all → the whole reply is the file
    changes, content = parse_reply("just the file body")
    assert changes is None and content == "just the file body"
    # header but no --- → content still starts after the header line
    changes, content = parse_reply("CHANGES: tweak\nbody line")
    assert changes == "tweak" and content == "body line"
    # one outer code fence unwraps
    _c, content = parse_reply("CHANGES: x\n---\n```python\nprint(1)\n```")
    assert content == "print(1)"


async def test_propose_edit_returns_revision_and_prompts_with_content():
    llm = FakeLLM(["CHANGES: fixed\n---\n", "fixed ", "content"])
    revised, err = await propose_edit(llm, "notes.md", "old content", "fix it")
    assert revised == "fixed content" and err is None
    user = llm.messages[-1]["content"]
    assert "notes.md" in user and "old content" in user and "fix it" in user


async def test_propose_edit_preserves_trailing_newline():
    llm = FakeLLM(["CHANGES: c\n---\nnew body"])
    revised, _err = await propose_edit(llm, "a.md", "old body\n", "change")
    assert revised == "new body\n"


async def test_propose_edit_too_large_never_calls_the_model():
    llm = FakeLLM(["x"])
    revised, err = await propose_edit(
        llm, "big.md", "x" * (MAX_EDIT_CHARS + 1), "trim")
    assert revised is None and "too large" in err
    assert llm.messages is None


async def test_propose_edit_honest_on_none_empty_and_unchanged():
    # the model says nothing matched → honest message, no diff
    revised, err = await propose_edit(
        FakeLLM(["CHANGES: none\n---\nsame"]), "a.md", "same", "change")
    assert revised is None and "nothing in the file" in err

    revised, err = await propose_edit(FakeLLM([""]), "a.md", "same", "change")
    assert revised is None and err

    # named a change but regurgitated the file verbatim → honest no-op
    revised, err = await propose_edit(
        FakeLLM(["CHANGES: c\n---\nsame"]), "a.md", "same", "change")
    assert revised is None and "no changes" in err
