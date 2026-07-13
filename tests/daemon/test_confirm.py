"""ConfirmBroker: approve/deny/timeout/disconnect semantics."""

import asyncio

from lumen.daemon.confirm import ConfirmBroker


async def test_resolve_approved():
    broker = ConfirmBroker()
    cid = broker.begin()
    task = asyncio.ensure_future(broker.wait(cid))
    await asyncio.sleep(0)
    assert broker.resolve(cid, True) is True
    assert await task is True


async def test_resolve_declined():
    broker = ConfirmBroker()
    cid = broker.begin()
    task = asyncio.ensure_future(broker.wait(cid))
    await asyncio.sleep(0)
    assert broker.resolve(cid, False) is True
    assert await task is False


async def test_timeout_denies():
    broker = ConfirmBroker(timeout=0.01)
    cid = broker.begin()
    assert await broker.wait(cid) is False


async def test_resolve_unknown_or_stale_id_is_ignored():
    broker = ConfirmBroker(timeout=0.01)
    assert broker.resolve(999, True) is False
    cid = broker.begin()
    await broker.wait(cid)               # times out, id cleaned up
    assert broker.resolve(cid, True) is False


async def test_deny_all_flushes_every_pending_confirm():
    broker = ConfirmBroker()
    c1, c2 = broker.begin(), broker.begin()
    t1 = asyncio.ensure_future(broker.wait(c1))
    t2 = asyncio.ensure_future(broker.wait(c2))
    await asyncio.sleep(0)
    broker.deny_all()
    assert await t1 is False and await t2 is False


async def test_ids_are_unique():
    broker = ConfirmBroker()
    assert broker.begin() != broker.begin()


async def test_resolve_passes_payload_through():
    # Phase 7 compose: the popup's answer is fields, not a yes/no.
    broker = ConfirmBroker()
    cid = broker.begin()
    task = asyncio.ensure_future(broker.wait(cid))
    await asyncio.sleep(0)
    assert broker.resolve(cid, {"subject": "s"}) is True
    assert await task == {"subject": "s"}


async def test_wait_per_call_timeout_overrides_default():
    # Editing an email in the compose popup outlives a confirm-click timeout.
    broker = ConfirmBroker(timeout=60)
    cid = broker.begin()
    assert await broker.wait(cid, timeout=0.01) is False
