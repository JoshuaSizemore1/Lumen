"""RuleStore CRUD + deterministic matchers."""
from lumen.daemon import db
from lumen.daemon.connectors.mail_rules import (
    RuleStore, matching_labels, rule_matches, sender_address)


def rule(**over):
    base = {"label": "BSA", "from_addrs": [], "domains": [],
            "subject_kw": [], "body_kw": []}
    base.update(over)
    return base


def row(**over):
    base = {"sender": "Troop 42 <news@Scouting.org>",
            "subject": "Pinewood Derby", "body": "See you at the pack meeting"}
    base.update(over)
    return base


def test_sender_address():
    assert sender_address("Ada <Ada@X.com>") == "ada@x.com"
    assert sender_address("bare@x.com") == "bare@x.com"
    assert sender_address("No Address Here") == ""


def test_matchers_any_condition_case_insensitive():
    assert rule_matches(rule(from_addrs=["NEWS@scouting.org"]), row())
    assert rule_matches(rule(domains=["scouting.org"]), row())
    assert rule_matches(rule(domains=["@scouting.org"]), row())      # tolerant
    assert rule_matches(rule(domains=["scouting.org"]),
                        row(sender="a <b@mail.scouting.org>"))       # subdomain
    assert not rule_matches(rule(domains=["couting.org"]), row())    # no suffix bleed
    assert rule_matches(rule(subject_kw=["pinewood"]), row())
    assert rule_matches(rule(body_kw=["PACK MEETING"]), row())
    assert not rule_matches(rule(subject_kw=["invoice"]), row())


def test_matching_labels_all_rules_dedup():
    rules = [rule(label="BSA", domains=["scouting.org"]),
             rule(label="Bills", subject_kw=["derby"]),
             rule(label="BSA", body_kw=["pack"])]
    assert matching_labels(rules, row()) == ["BSA", "Bills"]


def test_store_crud(tmp_path):
    store = RuleStore(db.connect(tmp_path / "r.db"))
    assert store.list_all() == []
    r = store.add(rule(subject_kw=["scout"]))
    assert r["id"] and r["enabled"] is True and r["subject_kw"] == ["scout"]
    assert store.get(r["id"])["label"] == "BSA"
    store.set_enabled(r["id"], False)
    assert store.enabled() == [] and store.list_all()[0]["enabled"] is False
    upd = store.update(r["id"], rule(label="Scouts", body_kw=["troop"]))
    assert upd["label"] == "Scouts" and upd["enabled"] is False   # update keeps enabled
    assert store.update(999, rule()) is None
    store.delete(r["id"])
    assert store.list_all() == []
