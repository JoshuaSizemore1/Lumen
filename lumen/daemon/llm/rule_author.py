"""Rule-authoring for the LOCAL model (a prompt asset, not a .claude skill):
turns a fuzzy 'filter X as Y' request into a concrete mail rule. Runs only on
an explicit create-a-rule request, then unloads — power budget. Mechanical
validation gate, book_recs rule-class."""

MAX_KW = 10
MAX_LABEL = 60


def validate_rule(p: dict) -> dict | None:
    """Mechanical gate: a usable rule needs a label and ≥1 condition. Lists
    are cleaned (strings only, stripped, deduped, capped) — never invented."""
    if not isinstance(p, dict):
        return None
    label = str(p.get("label") or "").strip().strip("/")
    if not label or len(label) > MAX_LABEL:
        return None

    def clean(key):
        out = []
        vals = p.get(key)
        for v in vals if isinstance(vals, list) else []:
            if not isinstance(v, str):
                continue
            v = v.strip()
            if v and v.casefold() not in (o.casefold() for o in out):
                out.append(v)
        return out[:MAX_KW]

    rule = {"label": label,
            **{c: clean(c) for c in ("from_addrs", "domains",
                                     "subject_kw", "body_kw")}}
    if not any(rule[c] for c in ("from_addrs", "domains",
                                 "subject_kw", "body_kw")):
        return None
    return rule
