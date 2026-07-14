"""Notes Q&A narration: passages + paths in, grounded streamed answer out."""

from lumen.daemon.llm.notes_qa import SYSTEM, build_notes_data, compose_answer

HITS = [
    {"path": "/home/u/Notes/net.md",
     "content": "The router password is hunter2.", "distance": 0.1},
    {"path": "/home/u/Notes/garden.md",
     "content": "Plant tomatoes in May.", "distance": 0.9},
]


def test_data_block_carries_paths_and_passages():
    data = build_notes_data(HITS)
    assert "FILE: /home/u/Notes/net.md" in data
    assert "hunter2" in data
    assert "FILE: /home/u/Notes/garden.md" in data


def test_system_prompt_demands_grounding_and_paths():
    low = SYSTEM.lower()
    assert "only" in low and "path" in low
    assert "say so" in low or "plainly" in low


class FakeLLM:
    def __init__(self):
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        yield "In net.md: hunter2."


async def test_compose_streams_with_question_and_passages():
    llm = FakeLLM()
    data = build_notes_data(HITS)
    chunks = [c async for c in compose_answer(llm, "router password?", data)]
    assert chunks == ["In net.md: hunter2."]
    assert llm.messages[0]["content"] == SYSTEM
    user = llm.messages[1]["content"]
    assert "router password?" in user and "hunter2" in user
