"""#44 (REOPENED 2026-08-30) — the Chrome-style "Save login?" prompt.

#44 was closed once on unit evidence, with an explicit caveat that it still
needed one live run against the real CAS + Duo form. That run happened and it
did not work. The keyring was, almost certainly, empty the whole time:

* the password was sampled once a second, so one typed-and-submitted inside a
  second — or pasted — was never read;
* `_maybe_save_login` returned silently when either half was missing, with no
  log line on that path at all;
* a cross-origin frame (Duo) cannot be read by construction;
* and the result was the message "no saved login — user types it", which on
  screen is indistinguishable from "the autofill is broken" — beside a
  pre-checked "Remember my login" box that made it look like it had worked.

The card is the structural fix, not the cosmetic one: **the fields are
editable**, so a failed capture is no longer fatal. Capture drops from "the
only way credentials are ever stored" to "a prefill convenience".
"""
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QLineEdit

from tests.ui.test_canvas_screen import FakeCanvasState

from lumen.ui_v3 import canvas_creds
from lumen.ui_v3.screens.canvas import CanvasScreen, SaveLoginCard


@pytest.fixture(autouse=True)
def _clean_prefs():
    QSettings("lumen", "ui_v3").remove("canvas/never_save")
    yield
    QSettings("lumen", "ui_v3").remove("canvas/never_save")


@pytest.fixture
def keyring(monkeypatch):
    """A keyring that records, so no test touches the real Secret Service."""
    box = {"creds": None, "saves": [], "forgets": 0}
    monkeypatch.setattr(canvas_creds, "load", lambda: box["creds"])
    monkeypatch.setattr(canvas_creds, "has_saved",
                        lambda default=False: box["creds"] is not None)

    def save(unid, password):
        box["saves"].append((unid, password))
        box["creds"] = (unid, password)
        return True

    def forget():
        box["forgets"] += 1
        box["creds"] = None
        return True

    monkeypatch.setattr(canvas_creds, "save", save)
    monkeypatch.setattr(canvas_creds, "forget", forget)
    return box


def _screen(qtbot):
    screen = CanvasScreen(FakeCanvasState())
    qtbot.addWidget(screen)
    return screen


def _card(screen) -> SaveLoginCard:
    return screen._save_card


# ---- the card appears at the confirmed-login moment ------------------------
def test_a_confirmed_login_offers_to_save(qtbot, keyring):
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "u1234567", "p": "s3cret"}
    screen._maybe_save_login()
    card = _card(screen)
    assert card is not None
    assert card.values() == ("u1234567", "s3cret")
    assert keyring["saves"] == []            # nothing written until Save


def test_a_capture_that_returned_nothing_still_gives_a_usable_card(qtbot, keyring):
    """The regression test for the entire failure mode. A password Lumen could
    not read — typed inside a Duo frame, or pasted between samples — used to
    mean silence forever. Now it means an empty box you type into."""
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "u1234567", "p": ""}
    screen._maybe_save_login()
    card = _card(screen)
    assert card is not None
    assert card.values() == ("u1234567", "")
    assert card.password.isEnabled()
    assert "couldn't read the password" in _text(card)


def test_a_capture_that_missed_both_halves_still_asks(qtbot, keyring):
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "", "p": ""}
    screen._maybe_save_login()
    assert _card(screen) is not None


def test_save_writes_the_edited_values_not_the_captured_ones(qtbot, keyring):
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "typo", "p": ""}
    screen._maybe_save_login()
    card = _card(screen)
    card.unid.setText("u1234567")
    card.password.setText("corrected")
    card._save()
    assert keyring["saves"] == [("u1234567", "corrected")]
    assert _card(screen) is None             # and the card goes away


def test_save_is_disabled_until_both_halves_are_present(qtbot, keyring):
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "u1234567", "p": ""}
    screen._maybe_save_login()
    card = _card(screen)
    assert card._save_btn.isEnabled() is False
    card.password.setText("x")
    assert card._save_btn.isEnabled() is True


def test_the_password_is_masked_with_a_reveal_toggle(qtbot, keyring):
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "u1234567", "p": "s3cret"}
    screen._maybe_save_login()
    card = _card(screen)
    assert card.password.echoMode() == QLineEdit.EchoMode.Password
    card._toggle_reveal()
    assert card.password.echoMode() == QLineEdit.EchoMode.Normal
    card._toggle_reveal()
    assert card.password.echoMode() == QLineEdit.EchoMode.Password


# ---- update vs. save -------------------------------------------------------
def test_a_changed_password_offers_an_update(qtbot, keyring):
    keyring["creds"] = ("u1234567", "old")
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "u1234567", "p": "new"}
    screen._maybe_save_login()
    assert "Update saved password?" in _text(_card(screen))


def test_an_unchanged_login_is_not_asked_about_again(qtbot, keyring):
    keyring["creds"] = ("u1234567", "same")
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "u1234567", "p": "same"}
    screen._maybe_save_login()
    assert _card(screen) is None


# ---- not now / never -------------------------------------------------------
def test_not_now_dismisses_without_remembering_the_refusal(qtbot, keyring):
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "u1234567", "p": "s3cret"}
    screen._maybe_save_login()
    _card(screen)._dismiss("not_now")
    assert _card(screen) is None
    assert keyring["saves"] == []
    screen._maybe_save_login()               # asked again next time
    assert _card(screen) is not None


def test_never_for_canvas_stops_it_asking(qtbot, keyring):
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "u1234567", "p": "s3cret"}
    screen._maybe_save_login()
    _card(screen)._dismiss("never")
    assert _card(screen) is None
    screen._maybe_save_login()
    assert _card(screen) is None
    assert CanvasScreen._never_save() is True


def test_never_is_reversible(qtbot, keyring):
    """It has to be: a decision you cannot undo from the UI is a trap."""
    CanvasScreen._set_never_save(True)
    screen = _screen(qtbot)
    screen._pending_creds = {"u": "u1234567", "p": "s3cret"}
    screen._maybe_save_login()
    assert _card(screen) is None
    CanvasScreen._set_never_save(False)
    screen._maybe_save_login()
    assert _card(screen) is not None


# ---- the visible saved/not-saved state -------------------------------------
def test_the_hero_says_when_nothing_is_saved(qtbot, keyring):
    screen = _screen(qtbot)
    assert "No saved login yet" in _text(screen._connect_hero())


def test_the_hero_says_when_something_is_saved(qtbot, keyring):
    keyring["creds"] = ("u1234567", "s3cret")
    screen = _screen(qtbot)
    text = _text(screen._connect_hero())
    assert "Saved login ready" in text and "Forget" in text


def test_saving_enables_the_fill_button(qtbot, keyring):
    screen = _screen(qtbot)
    assert screen._fill_btn.isEnabled() is False
    screen._pending_creds = {"u": "u1234567", "p": "s3cret"}
    screen._maybe_save_login()
    _card(screen)._save()
    assert screen._fill_btn.isEnabled() is True
    screen._forget()
    assert screen._fill_btn.isEnabled() is False


def _text(widget) -> str:
    from PyQt6.QtWidgets import QLabel, QLineEdit as _LE
    bits = [w.text() for w in widget.findChildren(QLabel)]
    bits += [w.placeholderText() for w in widget.findChildren(_LE)]
    return " ".join(bits)


# ---- Canvas settings: the state is visible and both decisions reversible ----
def test_settings_shows_not_saved(qtbot, keyring):
    screen = _screen(qtbot)
    screen._render_login_prefs()
    text = _text(screen._login_box.parentWidget())
    assert "not saved" in text and "Nothing stored" in text


def test_settings_shows_saved_and_can_forget(qtbot, keyring):
    keyring["creds"] = ("u1234567", "s3cret")
    screen = _screen(qtbot)
    screen._render_login_prefs()
    assert "saved" in _text(screen._login_box.parentWidget())
    screen._forget_from_settings()
    assert keyring["forgets"] == 1
    assert "not saved" in _text(screen._login_box.parentWidget())


def test_never_for_canvas_can_be_undone_from_settings(qtbot, keyring):
    """A decision you cannot reverse from the UI is a trap."""
    CanvasScreen._set_never_save(True)
    screen = _screen(qtbot)
    screen._render_login_prefs()
    assert "Not asking any more" in _text(screen._login_box.parentWidget())
    screen._allow_saving_again()
    assert CanvasScreen._never_save() is False
    assert "Not asking any more" not in _text(screen._login_box.parentWidget())
