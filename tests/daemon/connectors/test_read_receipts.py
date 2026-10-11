"""todo-fixes #110: mail opened offline was marked read in Lumen but never in
Gmail. Incremental sync only brings Gmail's own changes, so the old "the next
poll puts UNREAD back" never happened. Failed receipts now queue in the
mirror's state table and replay at the start of the next sync."""
from lumen.daemon.connectors.email_menu import PENDING_READ_KEY
from tests.daemon.connectors.test_email_menu_104 import (
    FakeExec, FakeService, make_sync, msg,
)


class Switchable(FakeService):
    """A Gmail whose modify() fails while `online` is False."""

    def __init__(self):
        super().__init__({})
        self.online = False
        real = self._messages.modify

        def modify(userId, id, body):
            if not self.online:
                return FakeExec(OSError("network is unreachable"))
            return real(userId, id, body)
        self._messages.modify = modify


def _setup(tmp_path):
    svc = Switchable()
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1), msg(2)])
    return svc, store, sync


async def test_an_offline_receipt_is_queued_and_sent_on_the_next_sync(tmp_path):
    svc, store, sync = _setup(tmp_path)
    store.update_labels("m1", add=[], remove=["UNREAD"])   # what the router does
    assert await sync.receipt_read("m1") is False
    assert '"m1"' in store.get_state(PENDING_READ_KEY)
    svc.online = True
    await sync.sync_once()
    assert ("m1", {"removeLabelIds": ["UNREAD"]}) in svc._messages.modify_calls
    assert store.get_state(PENDING_READ_KEY) is None
    assert store.get("m1")["is_read"]


async def test_still_offline_keeps_the_queue(tmp_path):
    svc, store, sync = _setup(tmp_path)
    await sync.receipt_read("m1")
    await sync.receipt_read("m2")
    await sync.receipt_read("m1")                 # no duplicates
    await sync._replay_read_receipts()
    assert sync._pending_reads() == ["m1", "m2"]


async def test_marking_unread_again_cancels_the_queued_receipt(tmp_path):
    svc, store, sync = _setup(tmp_path)
    await sync.receipt_read("m1")
    svc.online = True
    assert await sync.mark_read("m1", False)      # the user's later word wins
    await sync._replay_read_receipts()
    assert not store.get("m1")["is_read"]
    assert ("m1", {"removeLabelIds": ["UNREAD"]}) not in svc._messages.modify_calls


async def test_a_message_gone_from_the_mirror_is_dropped(tmp_path):
    svc, store, sync = _setup(tmp_path)
    await sync.receipt_read("m9")                 # never mirrored / since removed
    svc.online = True
    await sync._replay_read_receipts()
    assert sync._pending_reads() == []
    assert svc._messages.modify_calls == []


async def test_online_receipt_queues_nothing(tmp_path):
    svc, store, sync = _setup(tmp_path)
    svc.online = True
    assert await sync.receipt_read("m1") is True
    assert store.get_state(PENDING_READ_KEY) is None
