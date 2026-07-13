import gc

import pytest


@pytest.fixture(autouse=True)
def _qt_deterministic_teardown():
    """pytest-qt keeps only weakrefs, so Python GC tears down whole widget
    trees at arbitrary points — including while a later test spins the event
    loop, where a half-destructed widget receiving a paint event segfaults
    (live flake: 'wrapped C/C++ object deleted' → 'pure virtual method
    called'). Collect dead wrappers between tests, while no events are in
    flight, and flush the resulting deferred deletes."""
    yield
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    gc.collect()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()
