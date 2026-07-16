"""Rule authoring: mechanical validation gate + one-shot proposal parsing."""
import json

from lumen.daemon.llm.rule_author import propose_rule, validate_rule


class FakeLLM:
    def __init__(self, text):
        self._text = text
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        yield self._text


def test_valid_rule_round_trips_and_cleans():
    got = validate_rule({"label": " BSA ", "from_addrs": ["a@x.com", "A@X.COM", 7],
                         "domains": [" scouting.org "],
                         "subject_kw": ["scout", ""], "body_kw": None,
                         "create_label_if_missing": True})   # extra keys ignored
    assert got == {"label": "BSA", "from_addrs": ["a@x.com"],
                   "domains": ["scouting.org"], "subject_kw": ["scout"],
                   "body_kw": []}


def test_rejects_unusable_rules():
    assert validate_rule({}) is None                            # no label
    assert validate_rule({"label": "X"}) is None                # no conditions
    assert validate_rule({"label": "x" * 61,
                          "subject_kw": ["a"]}) is None         # label too long
    assert validate_rule("nope") is None                        # not a dict


def test_keyword_lists_capped():
    got = validate_rule({"label": "L", "subject_kw": [str(i) for i in range(20)]})
    assert len(got["subject_kw"]) == 10


async def test_propose_rule_parses_and_carries_labels():
    reply = json.dumps({"label": "BSA", "from_addrs": [], "domains": ["scouting.org"],
                        "subject_kw": ["boy scout", "troop"], "body_kw": ["scouting"]})
    llm = FakeLLM("Sure! Here you go: " + reply)   # prose-wrapped JSON still parses
    rule, err = await propose_rule(llm, "filter boy scouts stuff as BSA",
                                   ["Bills", "BSA"])
    assert err is None and rule["label"] == "BSA"
    assert rule["domains"] == ["scouting.org"]
    assert "Existing labels: Bills, BSA" in llm.messages[-1]["content"]


async def test_propose_rule_honest_failure_on_garbage():
    rule, err = await propose_rule(FakeLLM("no json here"), "make a rule", [])
    assert rule is None and "couldn't turn that into a rule" in err


async def test_propose_rule_rejects_conditionless_reply():
    rule, err = await propose_rule(
        FakeLLM('{"label": "X", "from_addrs": [], "domains": [], '
                '"subject_kw": [], "body_kw": []}'), "rule please", [])
    assert rule is None and err
