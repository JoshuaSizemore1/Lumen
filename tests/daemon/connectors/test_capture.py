"""Quick-capture classifier: launcher free text -> chat | capture | ambiguous.
Deterministic table; the LLM is only consulted for 'ambiguous' (router's job)."""

from lumen.daemon.connectors.capture import classify


def test_questions_and_interrogatives_are_chat():
    for msg in ("what's due this week?", "what projects do I have",
                "when is my next meeting", "can you find my resume",
                "how do I look today", "is the report done",
                "show me my inbox", "find the tax pdf",
                "tell me about my calendar", "will it rain"):
        assert classify(msg) == "chat", msg


def test_greetings_and_assistant_commands_are_chat():
    for msg in ("hi", "hey lumen", "hello", "thanks", "good morning",
                "add a todo: buy milk", "remind me to call the bank",
                "mark the dentist one done", "brief me", "recommend a book"):
        assert classify(msg) == "chat", msg


def test_short_imperative_fragments_are_capture():
    for msg in ("buy milk", "buy milk @tomorrow #errands",
                "dentist appointment friday", "renew car registration",
                "pick up the dry cleaning"):
        assert classify(msg) == "capture", msg
    # "email X about Y" stays chat on purpose: the compose route owns it —
    # opening a draft beats silently filing a todo.
    assert classify("email tax guy about w2") == "chat"


def test_grammar_tokens_are_a_strong_capture_signal():
    # @date/#tag means the user is speaking the todo grammar, even in a
    # longer sentence.
    assert classify("get the trailer hitch inspected before the trip "
                    "up north @friday #car") == "capture"


def test_long_plain_prose_is_ambiguous():
    assert classify("the thing about the garage door that keeps sticking "
                    "when it rains and needs some kind of adjustment "
                    "before winter comes") == "ambiguous"


def test_empty_is_chat():
    assert classify("") == "chat"
    assert classify("   ") == "chat"
