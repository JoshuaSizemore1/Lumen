"""Memory UI wiring: procedure refresh/approve/dismiss/remove through AppState,
and the Settings/Dashboard surfaces. Reuses the FakeClient seam from test_ui_v2."""
from lumen.ui_v2.state import AppState
from tests.ui.test_ui_v2 import FakeClient

PROC = {"slug": "m", "name": "Morning", "triggers": ["morning routine"],
        "last_used": None, "text": "# Morning"}


def test_refresh_procedures_populates():
    data = FakeClient()
    st = AppState(data=data)
    st.refresh_procedures()
    data.cb_for("memory.procedures")({"proposed": [PROC], "active": []})
    assert st.proposed_procedures[0]["slug"] == "m"


def test_approve_procedure_calls_daemon():
    data = FakeClient()
    st = AppState(data=data)
    st.approve_procedure("m")
    assert any(t == "memory.approve_procedure" and p == {"slug": "m"}
               for t, p, _cb in data.requests)
    data.cb_for("memory.approve_procedure")(
        {"ok": True, "proposed": [], "active": [PROC]})
    assert st.active_procedures[0]["slug"] == "m"


def test_dismiss_and_remove_procedure_call_daemon():
    data = FakeClient()
    st = AppState(data=data)
    st.dismiss_procedure("m")
    st.remove_procedure("m")
    types = [t for t, _p, _cb in data.requests]
    assert "memory.dismiss_procedure" in types
    assert "memory.remove_procedure" in types


def test_settings_screen_renders_procedures():
    from lumen.ui_v2.screens.settings import SettingsScreen
    st = AppState()   # sample mode
    st.proposed_procedures = [PROC]
    sc = SettingsScreen(st)
    sc._rebuild_procedures()   # must not raise; builds Approve/Dismiss rows


def test_dashboard_card_shows_when_proposed():
    from lumen.ui_v2.screens.dashboard import DashboardScreen
    st = AppState()   # sample mode
    dash = DashboardScreen(st)
    assert dash.proc_card.isHidden()
    st.proposed_procedures = [PROC]
    dash._refresh_proc_card()
    assert not dash.proc_card.isHidden()
    assert "Morning" in dash.proc_card_name.text()
