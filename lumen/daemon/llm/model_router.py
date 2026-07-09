"""Picks which model handles a request. Phase 3: always the fast model — a single
read-only tool call is within its ability. The escalation branch (14B for reliable
multi-step tool chains) lands in Phase 5/6; this is the seam it plugs into, so the
later change is config + one branch here, not a rewrite."""


class ModelRouter:
    def __init__(self, fast_model: str, escalation_model: str | None = None):
        self._fast = fast_model
        self._escalation = escalation_model

    def pick_model(self, message: str, *, needs_tools: bool) -> str:
        # Phase 3: no escalation yet. When multi-step chains appear, escalate here.
        return self._fast
