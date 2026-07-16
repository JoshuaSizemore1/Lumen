"""'Suggest labels': per-message verdict, only existing label names survive."""
from lumen.daemon.llm.label_suggest import parse_choice, suggest

LABELS = ["Bills", "BSA"]


def test_parse_choice_exact_and_casefold():
    assert parse_choice('{"label": "Bills"}', LABELS) == "Bills"
    assert parse_choice('{"label": "bills"}', LABELS) == "Bills"   # canonical name back
    assert parse_choice('Sure: {"label": "BSA"} hope that helps', LABELS) == "BSA"


def test_parse_choice_never_invents():
    assert parse_choice('{"label": null}', LABELS) is None
    assert parse_choice('{"label": "Newsletters"}', LABELS) is None  # not in list
    assert parse_choice('{"label": 3}', LABELS) is None
    assert parse_choice("no json", LABELS) is None
    assert parse_choice("", LABELS) is None


class FakeLLM:
    def __init__(self, text):
        self._text = text
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        yield self._text


async def test_suggest_grounds_on_row_and_labels():
    llm = FakeLLM('{"label": "Bills"}')
    row = {"sender": "Duke <billing@duke.com>", "subject": "Your statement",
           "snippet": "Amount due", "is_read": False,
           "received_at": "2026-07-15T10:00:00+00:00"}
    assert await suggest(llm, row, LABELS) == "Bills"
    assert "Bills, BSA" in llm.messages[0]["content"]      # labels in the system turn
    assert "Your statement" in llm.messages[-1]["content"]  # row grounds the user turn
