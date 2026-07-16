"""Rule-authoring for the LOCAL model (a prompt asset, not a .claude skill):
turns a fuzzy 'filter X as Y' request into a concrete mail rule. Runs only on
an explicit create-a-rule request, then unloads — power budget. Mechanical
validation gate, book_recs rule-class."""

from lumen.daemon.llm.event_create import parse_proposal

MAX_KW = 10
MAX_LABEL = 60

SYSTEM = (
    "You turn the user's request into a Gmail filing rule. Reply with ONLY a "
    "JSON object, no prose, shaped exactly:\n"
    '{"label": "...", "from_addrs": [], "domains": [], "subject_kw": [], '
    '"body_kw": []}\n'
    "The rule files matching mail under the label. Expand the user's concept "
    "into generous lowercase keyword lists for subject_kw and body_kw — "
    "synonyms, related words, organization names. Add sender domains only "
    "when you are confident; from_addrs only for addresses the user wrote. "
    "Prefer one of the user's existing labels when one fits; otherwise a "
    "short new label name.\n"
    'Example — "filter all emails relating to boy scouts as BSA":\n'
    '{"label": "BSA", "from_addrs": [], "domains": ["scouting.org"], '
    '"subject_kw": ["boy scout", "cub scout", "scouting", "troop", "BSA"], '
    '"body_kw": ["boy scout", "cub scout", "scouting", "troop", "BSA"]}'
)


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


async def propose_rule(llm, message: str, labels: list[str]
                       ) -> tuple[dict | None, str | None]:
    """One structured generation on the fast model — no tools, no chain."""
    user = (f"Existing labels: {', '.join(labels)}\n\nRequest: {message}"
            if labels else message)
    text = ""
    async for chunk in llm.chat([{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": user}]):
        text += chunk
    rule = validate_rule(parse_proposal(text) or {})
    if rule is None:
        return None, ("I couldn't turn that into a rule — tell me what to "
                      "match (a sender, a domain, or keywords) and what "
                      "label to file it under.")
    return rule, None
