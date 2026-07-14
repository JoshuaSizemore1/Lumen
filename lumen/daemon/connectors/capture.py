"""Quick-capture intent classifier: is launcher free text a todo to save or a
message for the assistant? Deterministic and cheap — the router only pays for
an LLM opinion on 'ambiguous', and a wrong 'capture' costs one Undo click."""

import re

# First-word signals that the user is talking TO the assistant, not jotting a
# note: interrogatives, auxiliaries, and command verbs the chat routes own
# ("add a todo:" and "mark … done" are chat-path features, not raw captures).
CHAT_OPENERS = frozenset(
    "what when where why how who whose which whats hows "
    "can could would should shall may do does did is are am was were will "
    "have has had "
    "find show search tell explain list open read give summarize summarise "
    "check look help write draft send compose email reply recommend suggest "
    "schedule book brief add remind mark delete remove undo".split())

GREETINGS = frozenset(
    "hi hey hello yo thanks thank ok okay good morning afternoon evening "
    "night bye goodbye".split())

GRAMMAR_TOKEN = re.compile(r"(?:^|\s)[@#]\w")

CAPTURE_MAX_WORDS = 8   # short imperative fragments read as notes


def classify(text: str) -> str:
    """-> 'chat' | 'capture' | 'ambiguous'."""
    t = text.strip()
    if not t:
        return "chat"
    if "?" in t:
        return "chat"
    words = re.split(r"[\s,:;!]+", t)
    first = re.sub(r"[^\w']", "", words[0].lower())
    if first in GREETINGS:
        return "chat"
    if first in CHAT_OPENERS:
        return "chat"
    if GRAMMAR_TOKEN.search(t):
        return "capture"    # @date/#tag is the todo grammar — user intent is explicit
    if len(words) <= CAPTURE_MAX_WORDS:
        return "capture"
    return "ambiguous"
