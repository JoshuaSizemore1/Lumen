"""Deterministic inbox rules: RuleStore (SQLite CRUD over mail_rules) + pure
matchers. Rules run in the sync path on newly-arrived mail — zero model cost
(design of record 2026-07-15); their writes were pre-authorized at creation."""

import json
import re
from datetime import datetime

_ADDR = re.compile(r"<([^<>\s]+@[^<>\s]+)>")
_LIST_COLS = ("from_addrs", "domains", "subject_kw", "body_kw")


def sender_address(sender: str) -> str:
    m = _ADDR.search(sender or "")
    if m:
        return m.group(1).casefold()
    s = (sender or "").strip()
    return s.casefold() if "@" in s else ""


def rule_matches(rule: dict, msg: dict) -> bool:
    """ANY condition hits: exact address, domain suffix, or case-insensitive
    substring on subject/body."""
    addr = sender_address(msg.get("sender", ""))
    if addr and addr in (a.casefold() for a in rule["from_addrs"]):
        return True
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    for d in rule["domains"]:
        d = d.casefold().lstrip("@")
        if d and (domain == d or domain.endswith("." + d)):
            return True
    subject = (msg.get("subject") or "").casefold()
    if any(k.casefold() in subject for k in rule["subject_kw"] if k):
        return True
    body = (msg.get("body") or "").casefold()
    return any(k.casefold() in body for k in rule["body_kw"] if k)


def matching_labels(rules: list[dict], msg: dict) -> list[str]:
    """Gmail-filter semantics: every matching rule applies; dedup, rule order."""
    out = []
    for r in rules:
        if r["label"] not in out and rule_matches(r, msg):
            out.append(r["label"])
    return out


class RuleStore:
    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _to_dict(row) -> dict:
        d = dict(row)
        for c in _LIST_COLS:
            d[c] = json.loads(d[c] or "[]")
        d["enabled"] = bool(d["enabled"])
        return d

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM mail_rules ORDER BY created_at, id").fetchall()
        return [self._to_dict(r) for r in rows]

    def enabled(self) -> list[dict]:
        return [r for r in self.list_all() if r["enabled"]]

    def get(self, rid: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM mail_rules WHERE id = ?", (rid,)).fetchone()
        return self._to_dict(row) if row else None

    def add(self, rule: dict) -> dict:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO mail_rules (label, from_addrs, domains, subject_kw, "
                "body_kw, enabled, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (rule["label"], *(json.dumps(rule[c]) for c in _LIST_COLS),
                 datetime.now().isoformat(timespec="seconds")))
        return self.get(cur.lastrowid)

    def update(self, rid: int, rule: dict) -> dict | None:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE mail_rules SET label = ?, from_addrs = ?, domains = ?, "
                "subject_kw = ?, body_kw = ? WHERE id = ?",
                (rule["label"], *(json.dumps(rule[c]) for c in _LIST_COLS), rid))
        return self.get(rid) if cur.rowcount else None

    def set_enabled(self, rid: int, enabled: bool) -> None:
        with self._conn:
            self._conn.execute("UPDATE mail_rules SET enabled = ? WHERE id = ?",
                               (int(enabled), rid))

    def delete(self, rid: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM mail_rules WHERE id = ?", (rid,))
