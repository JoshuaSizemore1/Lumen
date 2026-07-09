from lumen.daemon.llm.model_router import ModelRouter


def test_pick_model_returns_fast_model_for_now():
    mr = ModelRouter("qwen3:4b-instruct", escalation_model="qwen3:14b")
    assert mr.pick_model("who wrote Dune?", needs_tools=True) == "qwen3:4b-instruct"
    assert mr.pick_model("2+2", needs_tools=False) == "qwen3:4b-instruct"
