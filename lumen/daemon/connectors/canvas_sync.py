"""Background poller: pull active-course assignments + announcements into the
mirror on a timer. A plain API client on a timer — the poller must NEVER wake the
LLM (spec: bulk sync is never routed through the model). Mirrors CalendarSync /
GmailSync: injectable client_factory for tests, Canvas HTTP in asyncio.to_thread,
SQLite writes on the loop thread.

The session cookies arrive from the UI login window over IPC (Part 3) via
set_session(); the daemon never sees the password. A 401 mid-sync marks the
session dead so the UI knows to re-login; the mirror is left stale, never wiped."""

import asyncio
import logging
from datetime import datetime

from lumen.daemon.connectors.canvas_client import CanvasClient, CanvasSessionExpired
from lumen.daemon.connectors.canvas_reconcile import reconcile_todos
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.llm.canvas_flag import flag_announcements

log = logging.getLogger("lumen.daemon")


class CanvasSync:
    def __init__(self, store: CanvasStore, canvas_cfg, *,
                 todos=None, llm=None, client_factory=None):
        self._store = store
        self._cfg = canvas_cfg
        self._todos = todos
        self._llm = llm
        self._cookies: dict[str, str] | None = None
        self._session_alive = False
        self._last_sync: str | None = None
        self._client_factory = client_factory or self._build_client
        self._sync_lock = asyncio.Lock()

    @property
    def store(self) -> CanvasStore:
        return self._store

    # --- session handoff (the IPC route in Part 3 calls these) ---
    def set_session(self, cookies: dict[str, str]) -> None:
        self._cookies = dict(cookies)
        self._session_alive = True

    def clear_session(self) -> None:
        self._cookies = None
        self._session_alive = False

    @property
    def connected(self) -> bool:
        return self._cookies is not None and self._session_alive

    @property
    def busy(self) -> bool:
        return self._sync_lock.locked()

    def last_sync(self) -> str | None:
        return self._last_sync

    def _build_client(self) -> CanvasClient | None:
        if self._cookies is None:
            return None
        return CanvasClient.with_cookies(self._cfg.base_url, self._cookies)

    async def sync_once(self) -> bool:
        """True on a successful refresh; False keeps the stale mirror untouched
        (not connected, session dead, or a transient error)."""
        async with self._sync_lock:
            fetched = await asyncio.to_thread(self._fetch_blocking)
            if fetched is None:
                return False
            courses, assignments, announcements = fetched
            # SQLite writes on the loop thread — the daemon's single-writer rule.
            self._store.upsert_courses(courses)
            self._store.deactivate_courses_except([c["id"] for c in courses])
            self._store.upsert_assignments(assignments)
            self._store.upsert_announcements(announcements)
            self._last_sync = datetime.now().isoformat(timespec="seconds")
            if self._todos is not None:
                # Ungated: local todos are not an external write. Failures here
                # keep the fresh mirror — the next sync retries reconciliation.
                try:
                    reconcile_todos(self._store, self._todos)
                except Exception:
                    log.exception("canvas reconcile failed — mirror kept")
                if self._llm is not None:
                    # The one allowed model touch: bounded classification of the
                    # NEW announcements only (spec). Never fails the sync.
                    try:
                        await flag_announcements(self._store, self._llm)
                    except Exception:
                        log.exception("canvas announcement flag failed")
            return True

    def _fetch_blocking(self):
        """All Canvas network here (worker thread). Returns
        (courses, assignments, announcements) or None. None means: not connected,
        session dead, or a transient error — the caller keeps the stale mirror."""
        if not self.connected:
            return None            # no session yet — a normal state every tick
        try:
            client = self._client_factory()
        except Exception:
            log.exception("could not build canvas client")
            return None
        if client is None:
            return None
        try:
            courses = client.courses("active")
            assignments: list[dict] = []
            announcements: list[dict] = []
            for c in courses:
                assignments.extend(client.assignments(c["id"]))
                announcements.extend(client.announcements(c["id"]))
            return courses, assignments, announcements
        except CanvasSessionExpired:
            log.info("canvas session expired — UI must re-login")
            self._session_alive = False
            return None
        except Exception:
            log.exception("canvas sync failed — keeping stale mirror")
            return None
        finally:
            client.close()

    async def poll_forever(self) -> None:
        """Daemon background task; cancellation is the shutdown path."""
        interval = self._cfg.poll_minutes * 60
        while True:
            try:
                await self.sync_once()
            except Exception:
                log.exception("canvas poll iteration failed")
            await asyncio.sleep(interval)
