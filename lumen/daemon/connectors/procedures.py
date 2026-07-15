"""Supervised learned procedures (Phase 9). The distiller drafts recurring
routines into proposed/; the user approves them into active/. A procedure only
sequences tools/routes that already exist — it never grants new capability.
Files are plain markdown, hand-editable; approval/dismissal is a file move."""

import logging
import re
from collections import Counter
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

_TRIGGERS = re.compile(r"^triggers:\s*(.*)$", re.IGNORECASE)
_LAST_USED = re.compile(r"^last-used:\s*(.*)$", re.IGNORECASE)
_SLUG = re.compile(r"[^a-z0-9]+")

DRAFT_SYSTEM = (
    "You draft a short, named routine for a private assistant from evidence that "
    "the user repeats the same sequence. Reply with ONLY:\n"
    "# <Short Name>\n"
    "triggers: <2-4 short phrases the user might say, separated by ; >\n"
    "\n"
    "1. <step referencing an action the assistant already does>\n"
    "2. <step>\n"
    "Keep it under 8 lines. Steps must only sequence existing abilities "
    "(briefing, todos, calendar, email, books, files) — invent no new tools."
)


def slugify(name: str) -> str:
    return _SLUG.sub("-", name.strip().lower()).strip("-")[:40] or "procedure"


def write_procedure(path: Path, name: str, triggers: list[str],
                    steps: list[str], last_used: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [f"# {name}", f"triggers: {'; '.join(triggers)}",
            f"last-used: {last_used}", ""]
    body += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    path.write_text("\n".join(body) + "\n")


def parse_procedure(path: Path) -> dict | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    name, triggers, last_used = path.stem, [], None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            name = s[2:].strip()
        elif (m := _TRIGGERS.match(s)):
            triggers = [t.strip().lower() for t in m.group(1).split(";") if t.strip()]
        elif (m := _LAST_USED.match(s)):
            last_used = m.group(1).strip() or None
    return {"slug": path.stem, "name": name, "triggers": triggers,
            "last_used": last_used, "text": text}


class ProcedureStore:
    def __init__(self, root: Path, cfg, llm=None):
        self._root = root
        self._cfg = cfg
        self._llm = llm

    def _dir(self, state: str) -> Path:
        return self._root / state

    def _list(self, state: str) -> list[dict]:
        d = self._dir(state)
        if not d.is_dir():
            return []
        out = [parse_procedure(p) for p in sorted(d.glob("*.md"))]
        return [x for x in out if x is not None]

    def list_proposed(self) -> list[dict]:
        return self._list("proposed")

    def list_active(self) -> list[dict]:
        return self._list("active")

    def approve(self, slug: str) -> bool:
        src = self._dir("proposed") / f"{slug}.md"
        if not src.exists():
            return False
        if len(self.list_active()) >= self._cfg.max_active_procedures:
            return False
        dst = self._dir("active") / f"{slug}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        return True

    def dismiss(self, slug: str) -> bool:
        return self._unlink(self._dir("proposed") / f"{slug}.md")

    def remove(self, slug: str) -> bool:
        return self._unlink(self._dir("active") / f"{slug}.md")

    @staticmethod
    def _unlink(path: Path) -> bool:
        try:
            path.unlink()
            return True
        except OSError:
            return False

    def match(self, message: str) -> dict | None:
        low = message.lower()
        for proc in self.list_active():
            if any(t and t in low for t in proc["triggers"]):
                self._bump(proc["slug"])
                proc["last_used"] = date.today().isoformat()
                return proc
        return None

    def _bump(self, slug: str) -> None:
        path = self._dir("active") / f"{slug}.md"
        proc = parse_procedure(path)
        if proc is None:
            return
        steps = [l for l in proc["text"].splitlines() if re.match(r"^\d+\.", l.strip())]
        write_procedure(path, proc["name"], proc["triggers"],
                        [re.sub(r"^\d+\.\s*", "", s.strip()) for s in steps],
                        date.today().isoformat())

    async def propose_from_log(self, entries: list[dict], today: date) -> None:
        """Draft a proposal when the same route recurs ≥3×; also retire-propose
        active procedures unused past the cap. Best-effort — never raises."""
        if self._llm is None:
            return
        counts = Counter(
            (e["detail"].get("route") or e["subsystem"])
            for e in entries if isinstance(e.get("detail"), dict))
        existing = {p["slug"] for p in self.list_proposed() + self.list_active()}
        for route, n in counts.items():
            if n < 3:
                continue
            slug = slugify(f"{route}-routine")
            if slug in existing:
                continue
            await self._draft(route, [e for e in entries
                                      if (e["detail"].get("route") or e["subsystem"]) == route],
                              slug)
        self._retire_stale(today)

    async def _draft(self, route: str, entries: list[dict], slug: str) -> None:
        evidence = "\n".join(
            str(e["detail"].get("message") or e["detail"].get("action") or "")[:120]
            for e in entries[:20])
        text = ""
        try:
            async for chunk in self._llm.chat(
                    [{"role": "system", "content": DRAFT_SYSTEM},
                     {"role": "user", "content": f"Repeated around: {route}\n{evidence}"}]):
                text += chunk
        except Exception:
            return
        if not text.strip().startswith("# ") or len(text) > self._cfg.procedure_cap_chars:
            return
        path = self._dir("proposed") / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.strip() + f"\nlast-used: {date.today().isoformat()}\n")

    def _retire_stale(self, today: date) -> None:
        for proc in self.list_active():
            lu = proc.get("last_used")
            if not lu:
                continue
            try:
                seen = date.fromisoformat(lu)
            except ValueError:
                continue
            if (today - seen).days > self._cfg.procedure_retire_days:
                dst = self._dir("proposed") / f"retire-{proc['slug']}.md"
                dst.parent.mkdir(parents=True, exist_ok=True)
                write_procedure(dst, f"Retire: {proc['name']}",
                                ["approve to remove"],
                                [f"This routine hasn't been used since {lu}. "
                                 "Approve to retire it."], today.isoformat())
