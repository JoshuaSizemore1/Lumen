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

from lumen.daemon.connectors import canvas_calendar, canvas_session
from lumen.daemon.connectors import canvas_events
from lumen.daemon.connectors.canvas_client import CanvasClient, CanvasSessionExpired
from lumen.daemon.connectors.canvas_reconcile import reconcile_todos
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.llm.canvas_flag import flag_announcements

log = logging.getLogger("lumen.daemon")


class CanvasSync:
    def __init__(self, store: CanvasStore, canvas_cfg, *,
                 todos=None, llm=None, client_factory=None, session_path=None,
                 markers=None, prefs=None, queue=None, alerts=None,
                 proposals=None, events=None, calendar=None):
        self._store = store
        self._cfg = canvas_cfg
        self._todos = todos
        self._llm = llm
        self._cookies: dict[str, str] | None = None
        self._session_alive = False
        self._last_sync: str | None = None
        self._client_factory = client_factory or self._build_client
        # None keeps the old in-memory-only behaviour, so every existing test
        # construction is unaffected.
        self._session_path = session_path
        # Calendar sync (all optional, so every existing construction is
        # unchanged and the feature is inert until __main__ wires it up).
        self._markers = markers          # CalendarMarkerWriter
        self._prefs = prefs              # CanvasPrefs
        self._queue = queue              # CanvasQueue (proposed removals)
        self._alerts = alerts            # CanvasAlerts
        self._events = events            # EventStore, for conflict detection
        self._calendar = calendar        # CalendarSync, to re-read what we wrote
        self._proposals = proposals      # CanvasProposals (AI, never auto-created)
        self._sync_lock = asyncio.Lock()
        # Runtime Disable switch (#38); __main__ wires it to ConnectionState.
        self.paused = lambda: False

    @property
    def store(self) -> CanvasStore:
        return self._store

    # --- session handoff (the IPC route in Part 3 calls these) ---
    def set_session(self, cookies: dict[str, str]) -> None:
        self._cookies = dict(cookies)
        self._session_alive = True
        if self._session_path is not None:
            canvas_session.save(self._session_path, self._cookies)

    def clear_session(self) -> None:
        """Forget the session everywhere. Both canvas.disconnect and
        connections.disconnect already route through here, so they get the file
        deletion for free."""
        self._cookies = None
        self._session_alive = False
        if self._session_path is not None:
            canvas_session.clear(self._session_path)

    def restore_session(self) -> bool:
        """Load a persisted session at daemon start. The cookies are UNVALIDATED
        until the first sync — brief, because poll_forever syncs immediately, and
        self-healing, because a 401 there clears the file and the tab falls back
        to its Connect hero on its own."""
        if self._session_path is None:
            return False
        cookies = canvas_session.load(self._session_path)
        if not cookies:
            return False
        self._cookies = dict(cookies)
        self._session_alive = True
        log.info("canvas session restored from %s", self._session_path)
        return True

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
        (not connected, session dead, or a transient error).

        Two phases so archived courses cost zero network (#28): fetch the course
        list, decide the pull set on the loop thread (which respects the user's
        archive flags), then fetch items only for that set. All Canvas HTTP runs
        in worker threads; all SQLite stays on the loop thread."""
        async with self._sync_lock:
            if not self.connected:
                return False                   # no session yet — a normal tick
            try:
                client = self._client_factory()
            except Exception:
                log.exception("could not build canvas client")
                return False
            if client is None:
                return False
            try:
                courses = await asyncio.to_thread(self._fetch_courses, client)
                if courses is None:
                    self._drop_dead_session()
                    return False
                # SQLite on the loop thread — the daemon's single-writer rule.
                self._store.upsert_courses(courses)
                self._store.deactivate_courses_except([c["id"] for c in courses])
                pull_ids = set(self._store.pull_course_ids())
                pull = [c for c in courses if c["id"] in pull_ids]
                items = await asyncio.to_thread(self._fetch_items, client, pull)
                if items is None:
                    self._drop_dead_session()
                    return False
                assignments, announcements = items
            finally:
                client.close()
            # Microsecond precision on purpose: this is the identity of one
            # sync pass, and mark_missing compares against it. At second
            # granularity two syncs inside the same second are indistinguishable,
            # so a vanished assignment would never accrue its streak — which a
            # manual sync_now right after a poll hits routinely.
            stamp = datetime.now().isoformat()
            self._store.upsert_assignments(assignments, stamp)
            self._store.upsert_announcements(announcements)
            # Only courses we actually fetched may accuse their assignments of
            # having vanished — otherwise an archived or errored course would
            # make its whole assignment list look deleted.
            self._store.mark_missing([c["id"] for c in pull], stamp)
            self._last_sync = datetime.now().isoformat(timespec="seconds")
            # Calendar sync is independent of todo reconciliation — nesting it
            # under `todos` would silently disable it wherever todos aren't
            # wired. Never fails the sync: the mirror is the valuable part.
            try:
                await self._enrich()          # AI passes, before the diff runs
            except Exception:
                log.exception("canvas enrichment failed — mirror kept")
            try:
                await self._sync_calendar()
            except Exception:
                log.exception("canvas calendar sync failed — mirror kept")
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

    async def _enrich(self) -> dict:
        """The 'Lumen powered' passes. Off unless the AI switch is on, so the
        power budget is zero by default — the hard constraint on this laptop."""
        if (self._llm is None or self._prefs is None
                or not self._prefs.sync_enabled() or not self._prefs.ai_mode()):
            return {}
        from lumen.daemon.llm import canvas_enrich
        out = await canvas_enrich.classify_assignments(self._store, self._llm)
        if self._proposals is not None:
            out |= await canvas_enrich.find_exam_dates(
                self._store, self._llm, self._proposals)
            if self._events is not None:
                out |= canvas_enrich.propose_study_blocks(
                    self._store, self._proposals, self._events)
        return out

    async def _sync_calendar(self) -> dict:
        """The one external-write path in the poll loop. Google HTTP goes to a
        worker thread; every SQLite touch stays on this one."""
        if self._prefs is None:
            return {}
        actions = canvas_calendar.plan_calendar(self._store, self._prefs,
                                                datetime.now())
        if not actions:
            return {}
        conflicts = []
        if self._events is not None:
            try:
                conflicts = self._conflicts(actions)
            except Exception:
                log.exception("canvas conflict scan failed")
        results = await asyncio.to_thread(canvas_calendar.execute_calendar,
                                          self._markers, actions)
        counts = canvas_calendar.commit_calendar(
            self._store, self._queue, self._alerts, results, conflicts)
        log.info("canvas calendar: %s", counts)
        await self._refresh_calendar_cache(counts)
        return counts

    async def _refresh_calendar_cache(self, counts: dict) -> None:
        """Pull what we just wrote back into the local event cache (#59).

        These events are created straight on Google through CalendarMarkerWriter.
        The calendar screen never talks to Google — `calendar.list` only reads
        the cache CalendarSync fills — so until that cache is refreshed a new
        assignment simply does not exist as far as the UI is concerned. Its only
        refreshers were the 5-minute poll tick and daemon startup, which is why
        Josh's fix was to close Lumen entirely.

        Every user-initiated write in the router already re-syncs for exactly
        this reason ("show the new event promptly"); the background path was the
        one that never did. Gated on a real write so an idle tick — the common
        case — still costs nothing, and swallowed so a Google outage on the
        re-read can't cost us the Canvas mirror we just earned."""
        if self._calendar is None:
            return
        if not any(counts.get(k) for k in ("created", "updated", "recreated")):
            return
        try:
            await self._calendar.sync_once()
        except Exception:
            log.exception("calendar re-read after canvas write failed")

    def _conflicts(self, actions):
        """Deterministic overlap check against the cached calendar window — no
        model involved."""
        days = sorted({a.start[:10] for a in actions
                       if a.op != "remove" and a.start})
        if not days:
            return []
        existing = self._events.list_range(days[0], days[-1])
        return canvas_events.conflicts(actions, existing)

    def _drop_dead_session(self) -> None:
        """Loop-thread half of the 401 path. The fetch helpers run in a worker
        thread and only flip the in-memory flag; the persisted copy must go too,
        or a dead cookie is restored on every restart from here on — `connected`
        reporting True until each first sync fails, and the UI never telling the
        user to log in again."""
        if self._session_alive or self._cookies is None:
            return                              # transient error, not a 401
        self.clear_session()

    def _fetch_courses(self, client):
        """Phase 1 (worker thread): the enrolled-course list, or None on a dead
        session / transient error — the caller keeps the stale mirror."""
        try:
            return client.courses("active")
        except CanvasSessionExpired:
            log.info("canvas session expired — UI must re-login")
            self._session_alive = False
            return None
        except Exception:
            log.exception("canvas course fetch failed — keeping stale mirror")
            return None

    def _fetch_items(self, client, courses):
        """Phase 2 (worker thread): assignments + announcements for exactly the
        courses handed in (the archive-filtered pull set). Returns
        (assignments, announcements) or None on a dead session / error."""
        try:
            assignments: list[dict] = []
            announcements: list[dict] = []
            for c in courses:
                assignments.extend(client.assignments(c["id"]))
                announcements.extend(client.announcements(c["id"]))
            return assignments, announcements
        except CanvasSessionExpired:
            log.info("canvas session expired — UI must re-login")
            self._session_alive = False
            return None
        except Exception:
            log.exception("canvas sync failed — keeping stale mirror")
            return None

    async def poll_forever(self) -> None:
        """Daemon background task; cancellation is the shutdown path."""
        interval = self._cfg.poll_minutes * 60
        while True:
            await self._poll_iteration()
            await asyncio.sleep(interval)

    async def _poll_iteration(self) -> None:
        if self.paused():                # runtime Disable switch (#38)
            return
        try:
            await self.sync_once()
        except Exception:
            log.exception("canvas poll iteration failed")
