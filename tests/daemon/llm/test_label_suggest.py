"""'Suggest labels' v2: per-message verdict behind a confidence floor; only
existing label names rated a strong fit survive. Label descriptions are
derived deterministically from already-filed mail, never guessed."""
from lumen.daemon.llm.label_suggest import describe_label, parse_verdict, suggest

LABELS = ["Bills", "BSA"]


def test_parse_verdict_exact_and_casefold():
    assert parse_verdict('{"label": "Bills", "fit": "strong"}', LABELS) == "Bills"
    assert parse_verdict('{"label": "bills", "fit": "Strong"}', LABELS) == "Bills"
    assert parse_verdict('Sure: {"label": "BSA", "fit": "strong"} hope that helps',
                         LABELS) == "BSA"


def test_parse_verdict_confidence_floor():
    # Below the floor, suggest nothing rather than guess (new-features item 4):
    # a weak or missing fit means "not sure", never a suggestion.
    assert parse_verdict('{"label": "Bills", "fit": "weak"}', LABELS) is None
    assert parse_verdict('{"label": "Bills"}', LABELS) is None


def test_parse_verdict_never_invents():
    assert parse_verdict('{"label": null}', LABELS) is None
    assert parse_verdict('{"label": "Newsletters", "fit": "strong"}', LABELS) is None
    assert parse_verdict('{"label": 3, "fit": "strong"}', LABELS) is None
    assert parse_verdict("no json", LABELS) is None
    assert parse_verdict("", LABELS) is None


def test_describe_label_domains_and_subjects():
    rows = [{"sender": "Troop 148 <news@scouting.org>", "subject": "Pinewood Derby"},
            {"sender": "BSA <updates@scouting.org>", "subject": "Camp signup"},
            {"sender": "x <a@troop148.org>", "subject": ""}]
    d = describe_label("BSA", rows)
    assert "scouting.org" in d and "troop148.org" in d
    assert "Pinewood Derby" in d and "Camp signup" in d


def test_describe_label_without_mail():
    assert describe_label("BSA", []) == "nothing filed here yet"


class FakeLLM:
    def __init__(self, text):
        self._text = text
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        yield self._text


async def test_suggest_grounds_on_row_labels_and_descriptions():
    llm = FakeLLM('{"label": "Bills", "fit": "strong"}')
    row = {"sender": "Duke <billing@duke.com>", "subject": "Your statement",
           "snippet": "Amount due", "is_read": False,
           "received_at": "2026-07-15T10:00:00+00:00"}
    assert await suggest(llm, row, LABELS,
                         {"Bills": "mail from duke.com"}) == "Bills"
    sysmsg = llm.messages[0]["content"]
    assert "- Bills: mail from duke.com" in sysmsg   # description grounds the pick
    assert "- BSA" in sysmsg                         # description-less label still listed
    assert "topic-specific" in sysmsg                # generic-bucket ranking rule
    assert "Your statement" in llm.messages[-1]["content"]  # row grounds the user turn


async def test_suggest_weak_fit_suggests_nothing():
    llm = FakeLLM('{"label": "Bills", "fit": "weak"}')
    row = {"sender": "a <a@x.com>", "subject": "s", "snippet": "", "is_read": True,
           "received_at": "2026-07-15T10:00:00+00:00"}
    assert await suggest(llm, row, LABELS) is None
