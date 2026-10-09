"""#51 (build a dictionary from mail already in each label) and #46 (one run
labelled everything FIDELITY)."""
import pytest

from lumen.daemon.llm import label_keywords as lk


def row(sender, subject):
    return {"sender": sender, "subject": subject}


FILED = {
    "Fidelity": [row("alerts@fidelity.com", "Your quarterly statement is ready"),
                 row("alerts@fidelity.com", "Statement available"),
                 row("noreply@fidelity.com", "Account statement")],
    "School": [row("noreply@utah.edu", "CS 3505 grade posted"),
               row("prof@utah.edu", "Homework 3 due Friday"),
               row("advising@utah.edu", "Registration opens Monday")],
    "Shipping": [row("ship-confirm@amazon.com", "Your package has shipped"),
                 row("ship-confirm@amazon.com", "Delivered today")],
    "Empty": [],
}


@pytest.fixture
def profiles():
    return lk.build_profiles(FILED)


# ---- feature extraction ---------------------------------------------------
def test_terms_namespace_sender_and_subject_separately():
    t = lk.terms(row("Ada <alerts@fidelity.com>", "Fidelity statement ready"))
    assert "from:alerts@fidelity.com" in t
    assert "domain:fidelity.com" in t
    assert "subj:fidelity" in t and "subj:statement" in t
    # the domain and the word are different evidence and must not collide
    assert "fidelity.com" not in t


def test_terms_drop_mail_furniture_and_bare_numbers():
    t = lk.terms(row("a@x.com", "Your update about the 2026 thing"))
    assert "subj:your" not in t and "subj:about" not in t and "subj:the" not in t
    assert "subj:2026" not in t
    assert "subj:thing" in t


# ---- exclusivity ----------------------------------------------------------
def test_a_term_shared_by_every_label_carries_no_weight():
    """The structural answer to #46: a label cannot win on shared vocabulary."""
    shared = {"A": [row("x@a.com", "monthly newsletter")],
              "B": [row("y@b.com", "monthly newsletter")],
              "C": [row("z@c.com", "monthly newsletter")]}
    p = lk.build_profiles(shared)
    # present in all three, so worth a third of what a unique term would be
    assert p["A"]["subj:newsletter"] == pytest.approx(1 / 3)
    assert p["A"]["domain:a.com"] == pytest.approx(1.0)
    assert lk.classify(row("q@q.com", "monthly newsletter"), p) is None


def test_a_label_with_nothing_filed_can_never_win(profiles):
    assert profiles["Empty"] == {}
    assert lk.classify(row("a@fidelity.com", "statement"), profiles) != "Empty"


# ---- classification -------------------------------------------------------
def test_an_obvious_message_is_settled_without_the_model(profiles):
    assert lk.classify(row("alerts@fidelity.com",
                           "Your statement is ready"), profiles) == "Fidelity"
    assert lk.classify(row("prof@utah.edu", "Homework 4"), profiles) == "School"
    assert lk.classify(row("ship-confirm@amazon.com",
                           "Your package has shipped"), profiles) == "Shipping"


def test_an_unrecognised_message_falls_through_to_the_model(profiles):
    assert lk.classify(row("stranger@example.test", "Kayak trip?"),
                       profiles) is None


def test_a_tie_falls_through_rather_than_guessing():
    tied = {"A": [row("shared@both.test", "invoice")],
            "B": [row("shared@both.test", "invoice")]}
    p = lk.build_profiles(tied)
    assert lk.classify(row("shared@both.test", "invoice"), p) is None


def test_weak_evidence_falls_through(profiles):
    # one shared, unremarkable subject word is not enough on its own
    assert lk.classify(row("nobody@nowhere.test", "statement"),
                       profiles) is None


# ---- the degenerate-run guard (#46) ---------------------------------------
def test_a_run_that_collapses_onto_one_label_is_named():
    verdicts = {f"m{i}": "FIDELITY" for i in range(9)}
    verdicts["m9"] = "School"
    assert lk.degenerate_label(verdicts) == "FIDELITY"


def test_a_varied_run_is_not_flagged():
    assert lk.degenerate_label(
        {"m1": "A", "m2": "B", "m3": "C", "m4": "A", "m5": "D"}) is None


def test_a_tiny_run_is_never_flagged():
    """Three receipts all going to Bills is not a collapse, it is Tuesday."""
    assert lk.degenerate_label({"m1": "Bills", "m2": "Bills",
                                "m3": "Bills"}) is None
