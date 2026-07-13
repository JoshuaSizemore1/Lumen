"""email_compose: extraction gate — editable-draft softness, no invented recipients."""
from lumen.daemon.llm.email_compose import propose_email, revise_email, validate_draft


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
    assert d == {"to": [], "cc": [], "subject": "", "body": "", "reply_hint": None}


async def test_propose_email_parses_json_reply():
    llm = FakeLLM('{"to": ["a@b.co"], "cc": [], "subject": "S", "body": "B", '
                  '"reply_hint": "Ada engines"}')
    draft, err = await propose_email(llm, "reply to ada's engines email, a@b.co")
    assert err is None and draft["to"] == ["a@b.co"]
    assert draft["reply_hint"] == "Ada engines"
    assert "JSON" in llm.messages[0]["content"]


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
