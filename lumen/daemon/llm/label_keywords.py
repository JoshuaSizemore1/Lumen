"""The deterministic first pass for "suggest labels" (todo-fixes #51).

Josh's ask: build a dictionary from mail *already filed* under each label, use
it to do the easy ones outright, and only spend the local model on the ones it
cannot settle. That is both faster (a run becomes mostly local SQL) and more
honest — a term that only ever appears under one label is real evidence, where
a 4B's verdict on a one-line prompt is a guess.

Scoring is by **exclusivity**, which is the whole trick. A term's weight is how
much of that label's mail carries it, divided by how many labels carry it at
all. "amazon.com" under Shopping and nowhere else is decisive; "the", "your",
or "noreply@" appear everywhere and therefore weigh nothing. That is also the
structural answer to #46 (every message labelled FIDELITY): a label cannot win
on vocabulary it shares with every other label.

Nothing here writes anything. Both this pass and the model pass produce the
same reviewable suggestion, which Josh still accepts or denies one at a time.
"""

import re

# A term must clear this to be suggested at all, and must beat the runner-up by
# this factor. Both deliberately strict: falling through to the model is cheap,
# a wrong confident suggestion is not.
SCORE_FLOOR = 0.45
MARGIN = 1.6

# Words that carry no filing signal — mail furniture, not topic. Kept small on
# purpose: exclusivity scoring already neutralises anything that turns up under
# every label, so this list only needs to cover terms frequent enough to be
# noise inside a *single* label.
_STOP = frozenset("""
about after all also and any are been before being but can did for from get
has have here how info into its just like more most new not now off one only
our out over please re some such than that the their them then there these
they this those thru too under until update via was were what when where which
while who why will with would you your fwd fw
""".split())

_TOKEN = re.compile(r"[a-z0-9][a-z0-9'&+-]{2,}")
_ADDRESS = re.compile(r"[\w.+-]+@[\w.-]+")


def terms(row: dict) -> set[str]:
    """What one message contributes to (or is matched against) a profile: its
    sender address, its sender domain, and the distinctive subject words.

    Address and domain are namespaced so a subject word can never collide with
    a sender — "fidelity" the word and fidelity.com the domain are different
    pieces of evidence and should not reinforce each other by accident."""
    out: set[str] = set()
    m = _ADDRESS.search((row.get("sender") or "").lower())
    if m:
        addr = m.group(0)
        out.add(f"from:{addr}")
        out.add(f"domain:{addr.split('@', 1)[1]}")
    for word in _TOKEN.findall((row.get("subject") or "").lower()):
        if word not in _STOP and not word.isdigit():
            out.add(f"subj:{word}")
    return out


def build_profiles(filed: dict[str, list[dict]]) -> dict[str, dict[str, float]]:
    """Per-label term weights from mail already filed under each label.

    weight = (share of this label's mail carrying the term)
             / (number of labels carrying the term at all)

    So a term is worth most when it is both *typical* of a label and *unique*
    to it. A label with nothing filed under it gets an empty profile and can
    therefore never win the pre-pass — which is right: there is no evidence.
    """
    per_label: dict[str, dict[str, int]] = {}
    for name, rows in filed.items():
        counts: dict[str, int] = {}
        for row in rows:
            for term in terms(row):
                counts[term] = counts.get(term, 0) + 1
        per_label[name] = counts

    spread: dict[str, int] = {}
    for counts in per_label.values():
        for term in counts:
            spread[term] = spread.get(term, 0) + 1

    profiles: dict[str, dict[str, float]] = {}
    for name, counts in per_label.items():
        total = len(filed.get(name) or ())
        if not total:
            profiles[name] = {}
            continue
        profiles[name] = {t: (n / total) / spread[t] for t, n in counts.items()}
    return profiles


def score(row: dict, profiles: dict[str, dict[str, float]]) -> dict[str, float]:
    """Every label's evidence for this message, strongest term first."""
    found = terms(row)
    return {name: sum(weights[t] for t in found if t in weights)
            for name, weights in profiles.items()}


def classify(row: dict, profiles: dict[str, dict[str, float]], *,
             floor: float = SCORE_FLOOR,
             margin: float = MARGIN) -> str | None:
    """The label this message obviously belongs to, or None to ask the model.

    "Obviously" means two things at once: enough evidence in absolute terms,
    and clearly more than the runner-up. Ambiguity is not resolved here — it
    is handed on."""
    scores = score(row, profiles)
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, best_score = ranked[0]
    if best_score < floor:
        return None
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if runner_up and best_score < runner_up * margin:
        return None
    # One subject word is never enough on its own, however exclusive it is.
    # Who sent it is categorically stronger evidence than what it is called,
    # and a lone word is how a run starts filing an inbox under one bucket
    # (#46). Either the sender is known to the label, or at least two of its
    # words are.
    matched = [t for t in terms(row) if t in profiles[best]]
    if (len(matched) < 2
            and not any(t.startswith(("from:", "domain:")) for t in matched)):
        return None
    return best


# ---- the degenerate-run guard (#46) ---------------------------------------
# "One time when I ran the auto categorise emails, it just put everything as
# FIDELITY." Nothing detected that; the suggestions were simply offered.

DEGENERATE_SHARE = 0.6
DEGENERATE_MIN = 5


def degenerate_label(verdicts: dict[str, str], *,
                     share: float = DEGENERATE_SHARE,
                     minimum: int = DEGENERATE_MIN) -> str | None:
    """The label a run collapsed onto, if it collapsed.

    Applied to the *model's* verdicts only. A keyword verdict is evidence — if
    thirty receipts all really are from Fidelity, that is a correct answer, not
    a collapse. A model that answers the same thing for most of a varied inbox
    has anchored on one vivid label description instead of reading each
    message, and its verdicts for that label are worth less than nothing."""
    if len(verdicts) < minimum:
        return None
    counts: dict[str, int] = {}
    for name in verdicts.values():
        counts[name] = counts.get(name, 0) + 1
    name, n = max(counts.items(), key=lambda kv: kv[1])
    return name if n / len(verdicts) > share else None
