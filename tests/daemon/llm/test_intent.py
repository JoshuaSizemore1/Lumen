from lumen.daemon.llm import intent
from lumen.daemon.llm.client import LLMUnavailable


class FakeLLM:
    def __init__(self, reply="NONE", fail=False):
        self._reply, self._fail = reply, fail
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        if self._fail:
            raise LLMUnavailable("down")
        yield self._reply


async def test_single_label():
    assert await intent.classify(FakeLLM("email"), "any emals from Ada?") == {"email"}


async def test_multi_label_with_spacing_and_case():
    got = await intent.classify(FakeLLM(" Email, CALENDAR "), "mail about tmrw?")
    assert got == {"email", "calendar"}


async def test_none_and_garbage_yield_empty():
    assert await intent.classify(FakeLLM("NONE"), "hello!") == set()
    assert await intent.classify(FakeLLM("well, it depends…"), "hello!") == set()


async def test_unknown_labels_dropped_known_kept():
    assert await intent.classify(FakeLLM("email, weather"), "x") == {"email"}


async def test_llm_down_degrades_to_empty():
    assert await intent.classify(FakeLLM(fail=True), "x") == set()


async def test_prompt_carries_message_and_label_menu():
    llm = FakeLLM("NONE")
    await intent.classify(llm, "any emals from Ada?")
    # Framed as data to classify, not a message to answer (Claude, 2026-09-24).
    assert llm.messages[-1]["content"] == "Message to classify:\nany emals from Ada?"
    system = llm.messages[0]["content"]
    for label in intent.LABELS:
        assert label in system
