"""Performance guards for the ui_v3 shell.

These are the costs Josh actually feels on an iGPU-only laptop: clicking a
message, a background mail poll landing while he is on another screen, and
opening the app. Each was measured before the fix (188 ms / 144 ms / 424 ms
offscreen), so the thresholds here are generous multiples of the fixed cost —
they exist to catch a regression back to full-rebuild behaviour, not to police
milliseconds.
"""
import time

import pytest

from lumen.ui_v3.components import MailRow
from lumen.ui_v3.state import AppState


def _mails(n: int) -> list[dict]:
    return [{"id": f"m{i}", "from": f"sender{i}@example.com",
             "subj": f"Subject line {i}", "preview": "preview text " * 8,
             "time": "9:00", "unread": (i % 3 == 0), "body_html": None,
             "label_names": ["Work"] if i % 4 == 0 else []}
            for i in range(n)]


def _window(qtbot):
    # Shown, not merely constructed: a hidden screen defers its rebuilds by
    # design, which would make the reuse assertions below pass vacuously.
    from lumen.ui_v3.main import LumenWindow
    win = LumenWindow()
    qtbot.addWidget(win)
    win.resize(1280, 800)
    win.show()
    return win


# ---- the mail list reuses rows instead of rebuilding them ----------------
def test_selecting_a_message_reuses_the_existing_rows(qtbot):
    win = _window(qtbot)
    st = win.state
    st.mails = _mails(50)
    st.selected_mail = "m0"
    mail = win.screens["mail"]
    win.switch_to("mail")
    mail.rebuild()

    before = {id(r) for r in mail.findChildren(MailRow)}
    assert len(before) == 50

    st.select_mail("m7")
    qtbot.wait(20)                             # let the coalescer fire
    after = {id(r) for r in mail.findChildren(MailRow)}
    # Same row widgets — a rebuild would have replaced every one of them.
    assert after == before


def test_selection_moves_the_highlight_to_the_new_row(qtbot):
    win = _window(qtbot)
    st = win.state
    st.mails = _mails(10)
    st.selected_mail = "m0"
    mail = win.screens["mail"]
    win.switch_to("mail")
    mail.rebuild()

    rows = {r.mail_id: r for r in mail.findChildren(MailRow)}
    assert rows["m0"].is_selected() and not rows["m3"].is_selected()
    st.select_mail("m3")
    qtbot.wait(20)                             # the repaint is coalesced
    assert rows["m3"].is_selected() and not rows["m0"].is_selected()


def test_a_membership_change_still_rebuilds_the_list(qtbot):
    win = _window(qtbot)
    st = win.state
    st.mails = _mails(10)
    mail = win.screens["mail"]
    win.switch_to("mail")
    mail.rebuild()

    st.mails = _mails(4)
    mail.rebuild()
    assert len(mail.findChildren(MailRow)) == 4


def test_selecting_a_message_is_fast(qtbot):
    win = _window(qtbot)
    st = win.state
    st.mails = _mails(50)
    st.selected_mail = "m0"
    mail = win.screens["mail"]
    win.switch_to("mail")
    mail.rebuild()

    st.select_mail("m1")                       # warm the path
    qtbot.wait(20)
    # Each click is driven to completion — the repaint is coalesced onto the
    # next event-loop turn, so timing the call alone would measure nothing.
    t0 = time.perf_counter()
    for i in range(2, 12):
        st.select_mail(f"m{i}")
        qtbot.wait(1)
    per_click_ms = (time.perf_counter() - t0) / 10 * 1000
    # Was 188 ms per click when every selection rebuilt all 50 rows.
    assert per_click_ms < 40, f"{per_click_ms:.0f} ms per click"


# ---- hidden screens do not rebuild --------------------------------------
def test_a_mail_poll_does_not_rebuild_the_hidden_mail_screen(qtbot):
    win = _window(qtbot)
    st = win.state
    st.mails = _mails(50)
    mail = win.screens["mail"]
    win.switch_to("today")

    mail._rebuilds = 0
    st.mails_changed.emit()
    assert mail._rebuilds == 0, "hidden screen rebuilt on a background poll"
    assert mail._dirty is True


def test_a_hidden_screen_catches_up_when_shown(qtbot):
    win = _window(qtbot)
    st = win.state
    mail = win.screens["mail"]
    win.switch_to("today")
    st.mails = _mails(6)
    st.mails_changed.emit()

    win.switch_to("mail")
    assert mail._dirty is False
    assert len(mail.findChildren(MailRow)) == 6



# ---- screens are built on first visit ------------------------------------
def test_screens_are_not_all_built_at_startup(qtbot):
    win = _window(qtbot)
    built = [k for k, v in win._built.items() if v]
    assert "canvas" not in built and "calendar" not in built
    assert "today" in built           # the landing screen is built


def test_visiting_a_screen_builds_it_once(qtbot):
    win = _window(qtbot)
    win.switch_to("calendar")
    first = win.screens["calendar"]
    win.switch_to("today")
    win.switch_to("calendar")
    assert win.screens["calendar"] is first


def test_startup_is_fast(qtbot):
    from lumen.ui_v3.main import LumenWindow
    t0 = time.perf_counter()
    win = LumenWindow()
    ms = (time.perf_counter() - t0) * 1000
    qtbot.addWidget(win)
    # Was 424 ms with all nine screens constructed eagerly.
    assert ms < 250, f"{ms:.0f} ms to build the window"


# ---- redundant refreshes collapse ---------------------------------------
def test_a_burst_of_mail_changes_rebuilds_once(qtbot):
    win = _window(qtbot)
    st = win.state
    st.mails = _mails(20)
    mail = win.screens["mail"]
    win.switch_to("mail")

    mail._rebuilds = 0
    for _ in range(5):
        st.mails_changed.emit()
    qtbot.wait(50)
    assert mail._rebuilds == 1, f"{mail._rebuilds} rebuilds for one burst"
