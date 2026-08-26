"""email_compose: extraction gate — editable-draft softness, no invented recipients."""
from lumen.daemon.llm import writing_style
from lumen.daemon.llm.email_compose import (propose_email, revise_email,
                                            strip_em_dashes, validate_draft)


def test_strip_em_dashes_replaces_with_comma():
    # spaced em dash → clause comma; horizontal bar too
    assert strip_em_dashes("I'll be there — see you then.") == \
        "I'll be there, see you then."
    assert strip_em_dashes("the report—which was late—arrived") == \
        "the report, which was late, arrived"
    assert strip_em_dashes("a ― b") == "a, b"


def test_strip_em_dashes_tidies_punctuation_and_edges():
    assert strip_em_dashes("done — .") == "done."          # no ", ." soup
    assert strip_em_dashes("— Josh") == "Josh"             # leading dash line
    assert strip_em_dashes("plain text, no dash") == "plain text, no dash"
    assert strip_em_dashes("") == ""


def test_validate_draft_strips_em_dashes_from_lumen_text():
    d = validate_draft({"subject": "Update — Q3", "body": "Hi — thanks."},
                       user_message="whatever")
    assert d["subject"] == "Update, Q3"
    assert d["body"] == "Hi, thanks."


async def test_revise_email_strips_em_dashes():
    got, err = await revise_email(FakeLLM('{"subject": "S", "body": "Sure — done."}'),
                                  "s", "b", "i")
    assert err is None and got["body"] == "Sure, done."


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.messages = reply, None

    async def chat(self, messages):
        self.messages = messages
        yield self.reply


def test_validate_draft_keeps_only_addresses_the_user_wrote():
    p = {"to": ["sam@x.com", "made.up@spam.io", "Sam"], "cc": ["sam@x.com"],
         "subject": " hi ", "body": " text ", "reply_hint": ""}
    d = validate_draft(p, user_message="email sam@x.com please")
    assert d["to"] == ["sam@x.com"]          # invented + non-address dropped, not fatal
    assert d["cc"] == ["sam@x.com"]
    assert d["subject"] == "hi" and d["body"] == "text"
    assert d["reply_hint"] is None


def test_validate_draft_missing_keys_yield_editable_empties():
    d = validate_draft({}, user_message="whatever")
    assert d == {"to": [], "cc": [], "subject": "", "body": "",
                 "reply_hint": None, "to_hint": None}


async def test_propose_email_parses_json_reply():
    llm = FakeLLM('{"to": ["a@b.co"], "cc": [], "subject": "S", "body": "B", '
                  '"reply_hint": "Ada engines"}')
    draft, err = await propose_email(llm, "reply to ada's engines email, a@b.co")
    assert err is None and draft["to"] == ["a@b.co"]
    assert draft["reply_hint"] == "Ada engines"
    assert "JSON" in llm.messages[0]["content"]


async def test_propose_email_grounding_context_reaches_model_but_not_recipients():
    # #39: real calendar grounding is appended to the request so the model
    # writes from fact, but it must NOT be able to authorize a recipient — only
    # addresses in the ORIGINAL message survive validation.
    llm = FakeLLM('{"to": ["cal@x.com"], "cc": [], "subject": "S", '
                  '"body": "B", "reply_hint": null, "to_hint": null}')
    draft, err = await propose_email(
        llm, "email Sam about our meeting",
        context="Your events: Fri 2026-07-24 15:00 Budget sync (cal@x.com)")
    assert err is None
    # the grounding is in the user turn the model saw
    assert "Budget sync" in llm.messages[1]["content"]
    # but an address only present in the grounding is not a valid recipient
    assert draft["to"] == []


async def test_propose_email_unparseable_is_honest():
    draft, err = await propose_email(FakeLLM("sure, sending it now!"), "email bob")
    assert draft is None and "draft" in err


async def test_revise_email_returns_full_revision():
    llm = FakeLLM('{"subject": "Shorter", "body": "Hi."}')
    got, err = await revise_email(llm, "Long subject", "Long body", "shorter")
    assert err is None and got == {"subject": "Shorter", "body": "Hi."}
    assert "Long body" in llm.messages[1]["content"]


async def test_revise_email_empty_body_fails():
    got, err = await revise_email(FakeLLM('{"subject": "s", "body": ""}'), "s", "b", "i")
    assert got is None and "revision" in err


async def test_style_rules_ride_draft_and_revise_prompts(monkeypatch):
    monkeypatch.setattr(writing_style, "load_rules",
                        lambda path=None: "- Signs off Respectfully")
    llm = FakeLLM('{"to": [], "cc": [], "subject": "S", "body": "B", "reply_hint": null}')
    await propose_email(llm, "email sam about the demo")
    assert "Respectfully" in llm.messages[0]["content"]

    llm = FakeLLM('{"subject": "S", "body": "B"}')
    await revise_email(llm, "s", "b", "shorter")
    assert "Respectfully" in llm.messages[0]["content"]


async def test_no_style_file_leaves_prompts_bare(monkeypatch):
    monkeypatch.setattr(writing_style, "load_rules", lambda path=None: None)
    llm = FakeLLM('{"to": [], "cc": [], "subject": "S", "body": "B", "reply_hint": null}')
    await propose_email(llm, "email sam about the demo")
    assert "sent mail" not in llm.messages[0]["content"]
