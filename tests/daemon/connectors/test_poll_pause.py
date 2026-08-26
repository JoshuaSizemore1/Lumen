"""Runtime Disable (#38): a paused connection's poll iteration must skip its
sync but leave the loop alive so re-enabling resumes on the next tick."""
import pytest

from lumen.daemon.config import GoogleConfig, SyncConfig
from lumen.daemon.connectors.email_menu import EmailStore, GmailSync
from lumen.daemon.connectors.gcal import CalendarSync, EventStore
from lumen.daemon import db


def _mail(tmp_path):
    conn = db.connect(tmp_path / "e.db")
    sync = GmailSync(EmailStore(conn), GoogleConfig(), SyncConfig(),
                     service_factory=lambda: (_ for _ in ()).throw(
                         AssertionError("sync_once must not run while paused")))
    return sync


async def test_gmail_paused_skips_sync(tmp_path):
    sync = _mail(tmp_path)
    ran = []
    async def boom():
        ran.append(1)
    sync.sync_once = boom
    sync.paused = lambda: True
    await sync._poll_iteration()
    assert ran == []                     # skipped
    sync.paused = lambda: False
    await sync._poll_iteration()
    assert ran == [1]                    # resumes


async def test_calendar_paused_skips_sync(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    sync = CalendarSync(EventStore(conn), GoogleConfig(), SyncConfig())
    ran = []
    async def boom():
        ran.append(1)
    sync.sync_once = boom
    sync.paused = lambda: True
    await sync._poll_iteration()
    assert ran == []
    sync.paused = lambda: False
    await sync._poll_iteration()
    assert ran == [1]
