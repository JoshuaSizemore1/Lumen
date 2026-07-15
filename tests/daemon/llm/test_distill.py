from datetime import date

from lumen.daemon.llm import distill


def test_decay_drops_old_keeps_undated():
    now = date(2026, 7, 14)
    bullets = [
        "- Never books before 9am. (last seen 2026-07-10)",   # fresh
        "- Old habit. (last seen 2026-01-01)",                # stale > 60d
        "- No date bullet.",                                   # kept
    ]
    kept = distill.decay_bullets(bullets, now, 60)
    assert "- Never books before 9am. (last seen 2026-07-10)" in kept
    assert "- No date bullet." in kept
    assert all("Old habit" not in b for b in kept)


def test_validate_section_ok():
    body = "## Todos\n- Groups errands. (last seen 2026-07-14)\n"
    out = distill.validate_section(body, "Todos", 4000)
    assert out == "- Groups errands. (last seen 2026-07-14)"


def test_validate_rejects_wrong_header():
    assert distill.validate_section("## Books\n- x (last seen 2026-07-14)",
                                    "Todos", 4000) is None


def test_validate_rejects_no_dated_bullet():
    assert distill.validate_section("## Todos\n- undated", "Todos", 4000) is None


def test_validate_rejects_over_cap():
    body = "## Todos\n- " + "x" * 50 + " (last seen 2026-07-14)"
    assert distill.validate_section(body, "Todos", 20) is None


class FakeLLM:
    def __init__(self, reply):
        self._reply = reply

    async def chat(self, messages):
        yield self._reply


async def test_merge_section_success():
    now = date(2026, 7, 14)
    llm = FakeLLM("## Todos\n- Groups errands with #errands. (last seen 2026-07-14)\n")
    entries = [{"kind": "query", "detail": {"message": "add buy milk #errands"}}]
    out = await distill.merge_section(llm, "Todos", [], entries, now, 4000)
    assert out == ["- Groups errands with #errands. (last seen 2026-07-14)"]


def test_restamp_forces_valid_dates():
    now = date(2026, 7, 14)
    # model emitted a garbage placeholder date and an undated bullet
    new = ["- renew passport (last seen 0000-00-00)", "- buy ink"]
    out = distill.restamp_bullets(new, [], now)
    assert out == ["- renew passport (last seen 2026-07-14)",
                   "- buy ink (last seen 2026-07-14)"]


def test_restamp_reuses_prior_date_when_text_unchanged():
    now = date(2026, 7, 14)
    current = ["- Never books before 9am. (last seen 2026-06-01)"]
    new = ["- Never books before 9am. (last seen 0000-00-00)"]
    out = distill.restamp_bullets(new, current, now)
    assert out == ["- Never books before 9am. (last seen 2026-06-01)"]


async def test_merge_section_restamps_bad_model_date():
    now = date(2026, 7, 14)
    llm = FakeLLM("## Todos\n- renew passport (last seen 0000-00-00)\n")
    out = await distill.merge_section(llm, "Todos", [], [{"kind": "query",
                                                         "detail": {}}], now, 4000)
    assert out == ["- renew passport (last seen 2026-07-14)"]


async def test_merge_section_bad_output_returns_none():
    now = date(2026, 7, 14)
    llm = FakeLLM("I could not do that.")   # no valid section
    out = await distill.merge_section(llm, "Todos", ["- old (last seen 2026-07-01)"],
                                      [{"kind": "query", "detail": {}}], now, 4000)
    assert out is None
