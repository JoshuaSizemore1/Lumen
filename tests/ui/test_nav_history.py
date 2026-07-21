"""NavController — the pure, Qt-free history model behind the app-wide swipe
back/forward gesture (#18). Browser semantics: a linear history with a cursor,
where a fresh visit after going back discards the forward tail."""
from lumen.ui_v3.nav_history import NavController, NavEntry


def test_visit_appends_and_current_tracks():
    nav = NavController(NavEntry("today"))
    assert nav.current == NavEntry("today")
    nav.visit(NavEntry("mail"))
    assert nav.current == NavEntry("mail")
    nav.visit(NavEntry("calendar", ("week", "2026-07-20")))
    assert nav.current == NavEntry("calendar", ("week", "2026-07-20"))


def test_consecutive_duplicate_is_ignored():
    nav = NavController(NavEntry("today"))
    nav.visit(NavEntry("today"))            # same place: no new entry
    assert not nav.can_back()
    nav.visit(NavEntry("mail"))
    nav.visit(NavEntry("mail"))             # still deduped
    assert nav.can_back()
    nav.back()
    assert nav.current == NavEntry("today")
    assert not nav.can_back()


def test_back_and_forward_walk_history():
    nav = NavController(NavEntry("today"))
    nav.visit(NavEntry("mail"))
    nav.visit(NavEntry("calendar"))
    assert nav.back() == NavEntry("mail")
    assert nav.back() == NavEntry("today")
    assert not nav.can_back()
    assert nav.forward() == NavEntry("mail")
    assert nav.forward() == NavEntry("calendar")
    assert not nav.can_forward()


def test_visit_after_back_truncates_forward_tail():
    nav = NavController(NavEntry("today"))
    nav.visit(NavEntry("mail"))
    nav.visit(NavEntry("calendar"))
    nav.back()                              # now at "mail"
    assert nav.can_forward()
    nav.visit(NavEntry("files"))            # new branch discards "calendar"
    assert not nav.can_forward()
    assert nav.current == NavEntry("files")
    assert nav.back() == NavEntry("mail")


def test_back_at_start_and_forward_at_end_return_none():
    nav = NavController(NavEntry("today"))
    assert nav.back() is None
    assert nav.current == NavEntry("today")
    nav.visit(NavEntry("mail"))
    assert nav.forward() is None
    assert nav.current == NavEntry("mail")


def test_empty_controller_has_no_current():
    nav = NavController()
    assert nav.current is None
    assert not nav.can_back() and not nav.can_forward()
    nav.visit(NavEntry("today"))
    assert nav.current == NavEntry("today")


def test_cap_trims_oldest_entries():
    nav = NavController(cap=3)
    for key in ("a", "b", "c", "d", "e"):
        nav.visit(NavEntry(key))
    assert nav.current == NavEntry("e")
    # only the 3 most recent survive; walking back stops at "c"
    assert nav.back() == NavEntry("d")
    assert nav.back() == NavEntry("c")
    assert not nav.can_back()
