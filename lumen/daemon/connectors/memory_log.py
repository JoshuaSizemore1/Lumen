"""Tier-1 raw interaction log for the memory system (Phase 9). Written
fire-and-forget by the router as interactions complete; read only by the
background distiller and the forget path — never at query time. Corrections
are a distinct, stronger kind than routine queries."""

import json
import logging
import sqlite3
from datetime import datetime

log = logging.getLogger(__name__)

KINDS = ("query", "correction")
LAST_RUN_KEY = "memory_last_distill"


class MemoryLog:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def log(self, subsystem: str, kind: str, detail: dict) -> None:
        """Cheap append. Any failure is swallowed — memory must never break
        the answer path."""
        if kind not in KINDS:
            log.warning("memory_log: bad kind %r (dropped)", kind)
            return
        try:
            self._conn.execute(
                "INSERT INTO memory_log (ts, subsystem, kind, detail) "
                "VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), subsystem, kind,
                 json.dumps(detail, ensure_ascii=False)))
            self._conn.commit()
        except Exception:
            log.exception("memory_log write failed (swallowed)")

    def unfolded(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, ts, subsystem, kind, detail FROM memory_log "
            "WHERE folded = 0 ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d["detail"])
            except (json.JSONDecodeError, TypeError):
                d["detail"] = {}
            out.append(d)
        return out

    def unfolded_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) c FROM memory_log WHERE folded = 0").fetchone()["c"]

    def oldest_unfolded_age_hours(self, now: datetime) -> float | None:
        row = self._conn.execute(
            "SELECT ts FROM memory_log WHERE folded = 0 ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        try:
            then = datetime.fromisoformat(row["ts"])
        except ValueError:
            return None
        return (now - then).total_seconds() / 3600.0

    def recent_within(self, subsystem: str, kind: str, seconds: int,
                      now: datetime) -> bool:
        rows = self._conn.execute(
            "SELECT ts FROM memory_log WHERE subsystem = ? AND kind = ? "
            "ORDER BY id DESC LIMIT 5", (subsystem, kind)).fetchall()
        for r in rows:
            try:
                then = datetime.fromisoformat(r["ts"])
            except ValueError:
                continue
            if (now - then).total_seconds() <= seconds:
                return True
        return False

    def mark_folded(self, ids: list[int]) -> None:
        if not ids:
            return
        self._conn.executemany(
            "UPDATE memory_log SET folded = 1 WHERE id = ?", [(i,) for i in ids])
        self._conn.commit()

    def prune_folded(self, before_iso: str) -> None:
        self._conn.execute(
            "DELETE FROM memory_log WHERE folded = 1 AND ts < ?", (before_iso,))
        self._conn.commit()

    def delete_matching(self, topic: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM memory_log WHERE lower(detail) LIKE ?",
            (f"%{topic.lower()}%",))
        self._conn.commit()
        return cur.rowcount

    def last_run(self) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (LAST_RUN_KEY,)).fetchone()
        return row["value"] if row else None

    def set_last_run(self, ts_iso: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
            (LAST_RUN_KEY, ts_iso))
        self._conn.commit()
