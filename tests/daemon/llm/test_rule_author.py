"""Rule authoring: mechanical validation gate + one-shot proposal parsing."""
import json

from lumen.daemon.llm.rule_author import validate_rule


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
