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


# --- one dialog at a time; the model handed back while waiting (#94) --------

async def test_a_queued_confirms_timeout_starts_on_its_turn():
    # The UI shows one confirm and queues the next. The queued one's 120 s
    # used to run out behind the first, so the user approved a dead dialog.
    broker = ConfirmBroker(timeout=0.2)
    a, b = broker.begin(), broker.begin()
    wa = asyncio.ensure_future(broker.wait(a))
    wb = asyncio.ensure_future(broker.wait(b))
    await asyncio.sleep(0.15)
    broker.resolve(a, True)
    assert await wa is True
    await asyncio.sleep(0.15)       # 0.3 s in: b's timer, had it started, is up
    broker.resolve(b, True)
    assert await wb is True


async def test_an_open_compose_does_not_hold_the_dialog_slot():
    broker = ConfirmBroker()
    compose = broker.begin()
    wc = asyncio.ensure_future(broker.wait(compose, timeout=30, modal=False))
    await asyncio.sleep(0)
    cid = broker.begin()
    wd = asyncio.ensure_future(broker.wait(cid))
    await asyncio.sleep(0)
    broker.resolve(cid, True)
    assert await asyncio.wait_for(wd, 1) is True    # the confirm on top answers
    assert not wc.done()
    broker.resolve(compose, {"to": ["a@x.com"]})
    assert await wc == {"to": ["a@x.com"]}


async def test_waiting_hands_the_model_back_and_takes_it_again():
    from lumen.daemon import route_locks

    model = asyncio.Lock()
    await model.acquire()
    held = route_locks.Held()
    held.locks["model"] = model
    route_locks.current.set(held)
    broker = ConfirmBroker()
    cid = broker.begin()
    w = asyncio.ensure_future(broker.wait(cid))
    await asyncio.sleep(0)
    assert not model.locked()       # a model route can run meanwhile
    assert "model" not in held.locks
    broker.resolve(cid, True)
    assert await w is True
    assert model.locked() and held.locks["model"] is model


async def test_a_cancel_while_taking_the_model_back_leaves_nothing_held():
    from lumen.daemon import route_locks

    model = asyncio.Lock()
    await model.acquire()
    held = route_locks.Held()
    held.locks["model"] = model
    route_locks.current.set(held)
    gate = asyncio.Event()

    async def parker():
        async with route_locks.parked():
            await gate.wait()

    task = asyncio.ensure_future(parker())
    await asyncio.sleep(0)
    await model.acquire()           # another model route gets in
    gate.set()
    await asyncio.sleep(0)          # parker now waits to take it back
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # The server releases what `held` names; the other route's hold is safe.
    assert "model" not in held.locks
    model.release()
    assert not model.locked()
