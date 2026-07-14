"""Notes Q&A narration: the retrieved passages (with their file paths) are
the whole world — the model answers from them and names the files, so every
claim is checkable against a real note on disk."""

SYSTEM = (
    "You answer the user's question from their own notes. Passages from "
    "their note files are given below, each under its FILE path. Answer "
    "only from these passages and name the file path(s) the answer came "
    "from; quote short phrases where it helps. If the passages don't "
    "actually answer the question, say so plainly — never fill the gap "
    "from general knowledge. Keep it short."
)


def build_notes_data(hits: list[dict]) -> str:
    blocks = []
    for h in hits:
        blocks.append(f"FILE: {h['path']}\n{h['content']}")
    return "\n\n---\n\n".join(blocks)


async def compose_answer(llm, question: str, data: str):
    """One streaming narration pass on the fast model."""
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user",
                 "content": f"Question: {question}\n\nPassages from my notes:\n\n{data}"}]
    async for chunk in llm.chat(messages):
        yield chunk
