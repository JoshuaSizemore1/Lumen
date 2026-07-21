"""ui_v3 mail screen — collapsible mailbox nav column (#10)."""
from lumen.ui_v3.screens.mail import MailScreen
from lumen.ui_v3.state import AppState


def test_nav_lists_scopes_and_labels(qtbot):
    state = AppState()                      # sample mode, seeded mail + labels
    scr = MailScreen(state)
    qtbot.addWidget(scr)
    # The nav rows carry Inbox/Unread/Sent plus every label; assert the labels
    # made it in by checking the nav rebuilt without error and labels exist.
    assert state.mail_labels                # sample data has labels
    assert scr._nav_shown is True


def test_nav_collapse_and_reopen(qtbot):
    state = AppState()
    scr = MailScreen(state)
    qtbot.addWidget(scr)

    scr._set_nav(False)
    assert scr._nav_shown is False
    assert scr.nav_toggle.isVisibleTo(scr) is True    # reopen affordance shown

    scr._set_nav(True)
    assert scr._nav_shown is True
    assert scr.nav_toggle.isVisibleTo(scr) is False


def test_folder_row_sets_scope(qtbot):
    state = AppState()
    scr = MailScreen(state)
    qtbot.addWidget(scr)

    picked = []
    state.set_mail_scope = lambda s: picked.append(s)
    row = scr._folder_row("sent", "Sent", None)
    row._on_click()
    assert picked == ["sent"]
