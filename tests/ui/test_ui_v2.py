"""v2 UI wiring: AppState as the live daemon seam, confirm-over-IPC routing,
launcher streaming, and calendar/todo/book shape adaptation.

A FakeClient stands in for DaemonClient — same signals + methods, no socket."""
from PyQt6.QtCore import QObject, pyqtSignal

from lumen.ui_v2 import state as state_mod
from lumen.ui_v2.state import AppState, _norm_book, _norm_event, _norm_mail, _norm_todo


class FakeClient(QObject):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)
    tool_used = pyqtSignal(str)
    conversation = pyqtSignal(int)
    captured = pyqtSignal(dict)
    confirm_requested = pyqtSignal(dict)
    compose_requested = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.requests: list[tuple] = []
        self.sent: list[tuple] = []
        self.confirm_responses: list[tuple] = []

    def request(self, type_, payload, cb):
        self.requests.append((type_, payload, cb))

    def send(self, type_, payload):
        self.sent.append((type_, payload))

    def sleep_model(self):
        self.sent.append(("sleep", {}))

    def respond_confirm(self, confirm_id, approved):
        self.confirm_responses.append((confirm_id, approved))

    def cb_for(self, type_):
        return next(cb for t, _p, cb in self.requests if t == type_)


TODAY = state_mod.date.today()


# ---- pure normalizers ----------------------------------------------------

def test_norm_todo_groups_and_tags():
    overdue = _norm_todo({"id": 1, "text": "a", "completed": False,
                          "due_date": (TODAY).isoformat(), "tags": ["work", "home"]}, TODAY)
    assert overdue["group"] == "today" and overdue["due"] is None
    assert overdue["tags"] == ["work", "home"] and overdue["tag"] == "work"

    from datetime import timedelta
    future = _norm_todo({"id": 2, "text": "b", "completed": True,
                         "due_date": (TODAY + timedelta(days=3)).isoformat(),
                         "tags": []}, TODAY)
    assert future["group"] == "upcoming" and future["due"] and future["done"] is True

    none = _norm_todo({"id": 3, "text": "c", "completed": False,
                       "due_date": None, "tags": []}, TODAY)
    assert none["group"] == "none" and none["due"] is None and none["tag"] == ""


def test_norm_book_and_event():
    b = _norm_book({"id": 7, "title": "T", "author": None, "rating": None,
                    "notes": None, "date_finished": "2026-06-28"})
    assert b["author"] == "" and b["rating"] == 0 and b["done"] == "Jun 28"

    tz = state_mod.datetime.now().astimezone().tzinfo
    e = _norm_event({"title": "Sync", "all_day": False,
                     "start_at": "2026-07-11T09:30:00+00:00",
                     "end_at": "2026-07-11T10:30:00+00:00",
                     "calendar_name": "Work", "color": "#7aa2f7"}, tz)
    assert e["dur"] == 60 and e["cal"] == "Work" and e["color"] == "#7aa2f7"
    assert e["all_day"] is False and e["title"] == "Sync"

    allday = _norm_event({"title": "PTO", "all_day": True,
                          "start_at": "2026-07-20", "color": None}, tz)
    assert allday["all_day"] is True and allday["date"] == "2026-07-20"


def test_norm_event_carries_identity_for_delete():
    # The day-view delete button needs the Google event/calendar ids; sample
    # events have neither, which is what hides the button in sample mode.
    tz = state_mod.datetime.now().astimezone().tzinfo
    e = _norm_event({"id": "g1", "calendar_id": "work", "title": "Sync",
                     "all_day": False, "start_at": "2026-07-11T09:30:00+00:00",
                     "end_at": None, "color": None}, tz)
    assert e["id"] == "g1" and e["calendar_id"] == "work"


# ---- AppState live mode --------------------------------------------------

def test_live_appstate_loads_and_normalizes(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    assert st.live
    # __init__ kicks off the initial loads
    types = [t for t, _p, _cb in data.requests]
    assert "todos.list" in types and "books.list" in types and "books.recs" in types

    data.cb_for("todos.list")([
        {"id": 10, "text": "Call dentist", "completed": False, "due_date": None, "tags": ["personal"]}])
    assert st.todos[0]["text"] == "Call dentist" and st.todos[0]["tag"] == "personal"

    data.cb_for("books.list")([
        {"id": 5, "title": "Piranesi", "author": "Clarke", "rating": 4,
         "notes": "strange", "date_finished": None}])
    assert st.books[0]["title"] == "Piranesi" and st.books[0]["done"] == ""


def test_toggle_sends_completed_flip(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    data.cb_for("todos.list")([
        {"id": 10, "text": "x", "completed": False, "due_date": None, "tags": []}])
    data.requests.clear()
    st.toggle_todo(10)
    assert ("todos.toggle", {"id": 10, "completed": True}, st._set_todos) == data.requests[0]


def test_add_and_delete_todo_route_to_daemon(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    data.requests.clear()
    st.add_todo("  buy milk  ")
    assert data.requests[0][0] == "todos.add" and data.requests[0][1] == {"text": "buy milk"}
    st.add_todo("   ")  # blank ignored
    assert len(data.requests) == 1
    st.delete_todo(10)
    assert data.requests[1][0] == "todos.delete" and data.requests[1][1] == {"id": 10}


def test_confirm_over_ipc_routing(qtbot):
    data, chat, confirm = FakeClient(), FakeClient(), FakeClient()
    st = AppState(data=data, chat=chat, confirm=confirm)
    seen = []
    st.confirm_requested.connect(lambda p: seen.append(p))
    # a daemon confirm on either the data or the chat client surfaces the overlay
    chat.confirm_requested.emit({"confirm_id": 3, "title": "Create event", "rows": []})
    assert seen and seen[0]["confirm_id"] == 3
    st.respond_confirm(3, True)
    assert confirm.confirm_responses == [(3, True)]


def test_sleep_and_recommend(qtbot):
    data, chat = FakeClient(), FakeClient()
    st = AppState(data=data, chat=chat)
    st.sleep_model()
    assert ("sleep", {}) in chat.sent
    data.requests.clear()
    called = []
    st.recommend_books(on_done=lambda: called.append(True))
    assert data.requests[0][0] == "books.recommend"
    data.requests[0][2]({"recs": [{"title": "Solaris", "author": "Lem", "rationale": "mood"}]})
    assert st.recs[0]["why"] == "mood" and called == [True]


def test_fetch_calendar_sample_mode_carries_color(qtbot):
    st = AppState()  # sample mode
    got = {}
    st.fetch_calendar("2026-07-06", "2026-07-06", lambda r: got.update(r))
    assert got["connected"] and got["events"]
    assert all("color" in e and "start_min" in e for e in got["events"])


def test_dashboard_briefing_button_fetches_and_shows_panel(qtbot):
    from lumen.ui_v2.screens.dashboard import DashboardScreen
    data = FakeClient()
    st = AppState(data=data)
    sc = DashboardScreen(st)
    qtbot.addWidget(sc)
    assert not sc.brief_panel.isVisibleTo(sc)
    sc._run_briefing()
    assert not sc.brief_btn.isEnabled()      # busy state guards double-clicks
    sc._run_briefing()                       # second click while busy is a no-op
    cbs = [cb for t, _p, cb in data.requests if t == "briefing.today"]
    assert len(cbs) == 1
    cbs[0]({"text": "Good morning. One meeting at 9:30."})
    assert sc.brief_text.text() == "Good morning. One meeting at 9:30."
    assert sc.brief_panel.isVisibleTo(sc) and sc.brief_btn.isEnabled()
    sc._hide_briefing()
    assert not sc.brief_panel.isVisibleTo(sc)


def test_dashboard_briefing_error_unsticks_button(qtbot):
    from lumen.ui_v2.screens.dashboard import DashboardScreen
    data = FakeClient()
    st = AppState(data=data)
    sc = DashboardScreen(st)
    qtbot.addWidget(sc)
    sc._run_briefing()
    st.status_requested.emit("ollama is not reachable")   # daemon error path
    assert sc.brief_btn.isEnabled()


def test_fetch_briefing_sample_mode_is_synchronous(qtbot):
    got = []
    AppState().fetch_briefing(got.append)
    assert got and "daemon" in got[0]["text"]


def test_delete_event_routes_to_daemon(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    data.requests.clear()
    st.delete_event("e1", "primary", lambda r: None)
    assert data.requests[0][:2] == ("calendar.delete",
                                    {"id": "e1", "calendar_id": "primary"})


def daemon_event_row(**kw):
    import datetime as dt
    row = {"id": "g1", "calendar_id": "primary", "calendar_name": "Personal",
           "color": "#7986cb", "title": "Standup",
           "start_at": f"{dt.date.today().isoformat()}T09:30:00+00:00",
           "end_at": None, "all_day": False, "location": None,
           "description": None, "attendees": [], "status": "confirmed"}
    row.update(kw)
    return row


def test_calendar_day_agenda_delete_goes_through_daemon(qtbot):
    from lumen.ui_v2.screens.calendar import CalendarScreen
    from lumen.ui_v2.widgets import ClickLabel
    data = FakeClient()
    st = AppState(data=data)
    sc = CalendarScreen(st)
    qtbot.addWidget(sc)
    sc._set_view(2)                       # day view of today
    cb = [cb for t, _p, cb in data.requests if t == "calendar.list"][-1]
    cb({"events": [daemon_event_row()], "connected": True})
    xs = [w for w in sc.findChildren(ClickLabel) if w.text() == "✕"]
    assert len(xs) == 1                   # one agenda row -> one delete
    toasts = []
    st.toast_requested.connect(toasts.append)
    data.requests.clear()
    xs[0]._on_click()
    t, p, dcb = data.requests[0]
    assert (t, p) == ("calendar.delete", {"id": "g1", "calendar_id": "primary"})
    dcb({"deleted": True, "message": "Deleted: g1"})
    assert toasts and "Deleted" in toasts[0]
    assert any(t == "calendar.list" for t, _p, _cb in data.requests)   # re-read


def test_calendar_sample_events_show_no_delete(qtbot):
    from lumen.ui_v2.screens.calendar import CalendarScreen
    from lumen.ui_v2.widgets import ClickLabel
    sc = CalendarScreen(AppState())      # sample mode: events carry no id
    qtbot.addWidget(sc)
    sc._set_view(2)
    assert not [w for w in sc.findChildren(ClickLabel) if w.text() == "✕"]


# ---- launcher streaming --------------------------------------------------

def test_launcher_palette_streams(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    st = AppState()
    chat = FakeClient()
    pal = LauncherPalette(st, chat)
    qtbot.addWidget(pal)
    pal.input.setText("what's on today")
    pal._submit()
    assert chat.sent[0] == ("chat", {"message": "what's on today",
                                     "capture_ok": True})
    chat.chunk.emit("On ")
    chat.chunk.emit("today: standup.")
    assert pal.resp_text.text() == "On today: standup."
    chat.done.emit()
    assert "on-device" in pal.resp_foot.text()


def test_launcher_palette_shows_error(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    pal = LauncherPalette(AppState(), FakeClient())
    qtbot.addWidget(pal)
    pal.input.setText("hi")
    pal._submit()
    pal.chat.error.emit("daemon offline — start it with: uv run lumen-daemon")
    assert "offline" in pal.resp_text.text()


# ---- confirm overlay + window handler ------------------------------------

def test_confirm_overlay_fires_once_with_payload(qtbot):
    from PyQt6.QtWidgets import QWidget
    from lumen.ui_v2.confirm import ConfirmOverlay
    parent = QWidget()
    qtbot.addWidget(parent)
    ov = ConfirmOverlay(parent)
    got = []
    ov._on_done = lambda approved, payload: got.append((approved, payload))
    ov._payload = {"confirm_id": 9}
    ov._finish(True)
    ov._finish(False)  # second answer must be swallowed (esc-after-click, etc.)
    assert got == [(True, {"confirm_id": 9})]


def test_window_confirm_result_answers_daemon(qtbot):
    from lumen.ui_v2.main import LumenWindow
    data, confirm = FakeClient(), FakeClient()
    win = LumenWindow(AppState(data=data, confirm=confirm))
    qtbot.addWidget(win)
    # daemon confirm (has confirm_id): cancel and approve both answer the daemon
    win._on_confirm_result(False, {"confirm_id": 9})
    win._on_confirm_result(True, {"confirm_id": 12, "toast": "✓ done"})
    assert confirm.confirm_responses == [(9, False), (12, True)]
    # local mock confirm (no confirm_id): nothing sent to the daemon
    win._on_confirm_result(True, {"toast": "✓ mock"})
    assert len(confirm.confirm_responses) == 2


def test_warm_model_sends_warm_on_chat_client(qtbot):
    chat = FakeClient()
    st = AppState(data=FakeClient(), chat=chat)
    st.warm_model()
    assert ("warm", {}) in chat.sent
    # sample mode (no chat client) is a safe no-op
    AppState().warm_model()


def test_switch_to_launcher_warms_but_other_tabs_do_not(qtbot):
    from lumen.ui_v2.main import LumenWindow
    chat = FakeClient()
    win = LumenWindow(AppState(data=FakeClient(), chat=chat))
    qtbot.addWidget(win)
    chat.sent.clear()
    win.switch_to("dashboard")
    assert ("warm", {}) not in chat.sent
    win.switch_to("launcher")
    assert ("warm", {}) in chat.sent


def test_ui_model_label_matches_daemon_default():
    """The UI's model label must track the real configured model, not a leftover
    mockup string (the mock hardcoded 'llama3.1:8b' while the daemon ran qwen)."""
    from lumen.daemon.config import Config
    from lumen.ui_v2 import theme as T
    assert T.MODEL_NAME == Config().model


def _label_texts(widget) -> str:
    from PyQt6.QtWidgets import QLabel
    return " ".join(lb.text() for lb in widget.findChildren(QLabel))


# ---- conversation state helpers ------------------------------------------

def test_list_conversations_routes_and_sample_is_empty(qtbot):
    data = FakeClient()
    AppState(data=data).list_conversations(lambda r: None)
    assert data.requests[-1][0] == "conversations.list"
    sample = []
    AppState().list_conversations(sample.append)   # sample mode: synchronous []
    assert sample == [[]]


def test_get_conversation_routes(qtbot):
    data = FakeClient()
    AppState(data=data).get_conversation(4, lambda r: None)
    assert data.requests[-1][:2] == ("conversations.get", {"id": 4})


def test_delete_conversation_routes(qtbot):
    data = FakeClient()
    AppState(data=data).delete_conversation(4, lambda r: None)
    assert data.requests[-1][:2] == ("conversations.delete", {"id": 4})


# ---- launcher multi-turn + handoff ---------------------------------------

def test_launcher_followup_reuses_conversation_id(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    chat = FakeClient()
    pal = LauncherPalette(AppState(), chat)
    qtbot.addWidget(pal)
    pal.input.setText("first")
    pal._submit()
    # first turn: no id yet, capture offered
    assert chat.sent[0] == ("chat", {"message": "first", "capture_ok": True})
    chat.conversation.emit(2)
    chat.chunk.emit("a")
    chat.done.emit()
    pal.input.setText("second")
    pal._submit()
    assert chat.sent[1] == ("chat", {"message": "second", "conversation_id": 2})


def test_launcher_handoff_emits_open_chat(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    chat = FakeClient()
    st = AppState()
    pal = LauncherPalette(st, chat)
    qtbot.addWidget(pal)
    seen = []
    st.open_chat_requested.connect(seen.append)
    pal.input.setText("q")
    pal._submit()
    chat.conversation.emit(5)
    chat.chunk.emit("answer")
    chat.done.emit()
    pal._open_in_chat()
    assert seen == [5]


# ---- commitment suggestions (todos screen) ---------------------------------

SUGG = {"id": 5, "text": "send the report", "due_date": "2026-07-17",
        "quote": "I'll send the report over Friday", "email_id": "s1",
        "subject": "Re: report", "status": "pending"}


def test_suggestion_state_routes_and_accept_updates_todos(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    assert any(t == "todos.suggestions" for t, _p, _cb in data.requests)
    data.cb_for("todos.suggestions")({"suggestions": [SUGG]})
    assert st.suggestions[0]["text"] == "send the report"
    data.requests.clear()
    st.accept_suggestion(5)
    t, p, cb = data.requests[0]
    assert (t, p) == ("todos.accept_suggestion", {"id": 5})
    cb({"suggestions": [], "todos": [{"id": 9, "text": "send the report",
                                      "completed": False,
                                      "due_date": "2026-07-17", "tags": []}]})
    assert st.suggestions == [] and st.todos[0]["text"] == "send the report"
    st.dismiss_suggestion(4)
    assert data.requests[1][:2] == ("todos.dismiss_suggestion", {"id": 4})


def test_todos_screen_suggested_section_and_scan_busy(qtbot):
    from lumen.ui_v2.screens.todos import TodosScreen
    data = FakeClient()
    st = AppState(data=data)
    sc = TodosScreen(st)
    qtbot.addWidget(sc)
    data.cb_for("todos.suggestions")({"suggestions": [SUGG]})
    texts = _label_texts(sc)
    assert "send the report" in texts and "Re: report" in texts
    sc._scan()
    assert not sc.scan_btn.isEnabled()
    sc._scan()                              # double click is a no-op
    cbs = [cb for t, _p, cb in data.requests if t == "todos.scan_commitments"]
    assert len(cbs) == 1
    toasts = []
    st.toast_requested.connect(toasts.append)
    cbs[0]({"scanned": 3, "found": 1, "suggestions": [SUGG]})
    assert sc.scan_btn.isEnabled()
    assert toasts and "1 new suggestion" in toasts[0]


# ---- quick capture (launcher) ---------------------------------------------

def test_launcher_first_turn_offers_capture_but_followups_dont(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    chat = FakeClient()
    pal = LauncherPalette(AppState(), chat)
    qtbot.addWidget(pal)
    pal.input.setText("buy milk")
    pal._submit()
    assert chat.sent[0] == ("chat", {"message": "buy milk", "capture_ok": True})
    chat.conversation.emit(4)          # turned out to be a chat after all
    chat.chunk.emit("a")
    chat.done.emit()
    pal.input.setText("second")
    pal._submit()
    assert chat.sent[1] == ("chat", {"message": "second", "conversation_id": 4})


def test_launcher_captured_renders_toast_and_undo_deletes(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    data, chat = FakeClient(), FakeClient()
    st = AppState(data=data)
    pal = LauncherPalette(st, chat)
    qtbot.addWidget(pal)
    pal.input.setText("buy milk @tomorrow #errands")
    pal._submit()
    chat.captured.emit({"id": 9, "text": "buy milk",
                        "due_date": "2026-07-14", "tags": ["errands"]})
    chat.done.emit()
    assert "Added todo: buy milk" in pal.resp_text.text()
    assert "2026-07-14" in pal.resp_text.text()
    assert not pal._busy               # palette ready for the next input
    data.requests.clear()
    pal._undo_capture(9)
    assert data.requests[0][:2] == ("todos.delete", {"id": 9})


def test_launcher_captured_when_idle_is_ignored(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    pal = LauncherPalette(AppState(), FakeClient())
    qtbot.addWidget(pal)
    pal.chat.captured.emit({"id": 1, "text": "x", "due_date": None, "tags": []})
    assert pal.resp_text is None       # no turn in flight -> nothing rendered


# ---- full Chat screen ----------------------------------------------------

def test_chat_screen_submits_streams_and_threads_followup(qtbot):
    from lumen.ui_v2.screens.chat import ChatScreen
    data, chat = FakeClient(), FakeClient()
    sc = ChatScreen(AppState(data=data), chat_client=chat)
    qtbot.addWidget(sc)
    assert any(t == "conversations.list" for t, _p, _cb in data.requests)  # sidebar loads
    sc.input.setText("find my resume")
    sc._submit()
    assert chat.sent[0] == ("chat", {"message": "find my resume"})
    chat.conversation.emit(7)
    chat.chunk.emit("Found ")
    chat.chunk.emit("it.")
    assert sc.resp_text.text() == "Found it."
    chat.done.emit()
    sc.input.setText("delete it")
    sc._submit()
    assert chat.sent[1] == ("chat", {"message": "delete it", "conversation_id": 7})


def test_chat_screen_loads_past_conversation(qtbot):
    from lumen.ui_v2.screens.chat import ChatScreen
    data, chat = FakeClient(), FakeClient()
    sc = ChatScreen(AppState(data=data), chat_client=chat)
    qtbot.addWidget(sc)
    data.cb_for("conversations.list")([{"id": 3, "title": "calendar q", "updated_at": "x"}])
    sc.load_conversation(3)
    getcb = next(cb for t, p, cb in data.requests if t == "conversations.get" and p == {"id": 3})
    getcb({"conversation": {"id": 3}, "messages": [
        {"role": "user", "content": "what's on tuesday"},
        {"role": "assistant", "content": "two meetings"}]})
    assert sc._conv_id == 3
    texts = _label_texts(sc.thread)
    assert "what's on tuesday" in texts and "two meetings" in texts


def _last_is_stretch(lay) -> bool:
    return lay.count() > 0 and lay.itemAt(lay.count() - 1).spacerItem() is not None


def test_chat_thread_turns_pack_to_top(qtbot):
    # Without a trailing stretch the vbox spreads two turns across the whole
    # viewport (live bug 2026-07-13: giant gaps between messages).
    from lumen.ui_v2.screens.chat import ChatScreen
    sc = ChatScreen(AppState(data=FakeClient()), chat_client=FakeClient())
    qtbot.addWidget(sc)
    assert _last_is_stretch(sc.thread_lay)
    sc.input.setText("hi")
    sc._submit()
    assert _last_is_stretch(sc.thread_lay)      # turns insert before the stretch


def test_chat_thread_stretch_survives_reset_and_reload(qtbot):
    from lumen.ui_v2.screens.chat import ChatScreen
    data = FakeClient()
    sc = ChatScreen(AppState(data=data), chat_client=FakeClient())
    qtbot.addWidget(sc)
    sc.new_chat()
    assert _last_is_stretch(sc.thread_lay)
    sc.load_conversation(3)
    getcb = next(cb for t, p, cb in data.requests if t == "conversations.get" and p == {"id": 3})
    getcb({"conversation": {"id": 3}, "messages": [
        {"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]})
    assert _last_is_stretch(sc.thread_lay)


def test_launcher_thread_turns_pack_to_top(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    pal = LauncherPalette(AppState(), FakeClient())
    qtbot.addWidget(pal)
    pal.input.setText("hi")
    pal._submit()
    assert _last_is_stretch(pal.thread_lay)


def test_chat_screen_new_chat_resets(qtbot):
    from lumen.ui_v2.screens.chat import ChatScreen
    sc = ChatScreen(AppState(data=FakeClient()), chat_client=FakeClient())
    qtbot.addWidget(sc)
    sc._conv_id = 9
    sc.input.setText("hi")
    sc._submit()
    sc.new_chat()
    assert sc._conv_id is None and sc.resp_text is None


def test_chat_sidebar_rows_have_delete_affordance(qtbot):
    from lumen.ui_v2.screens.chat import ChatScreen
    from lumen.ui_v2.widgets import ClickLabel
    data = FakeClient()
    sc = ChatScreen(AppState(data=data), chat_client=FakeClient())
    qtbot.addWidget(sc)
    data.cb_for("conversations.list")([{"id": 3, "title": "q", "updated_at": "x"}])
    xs = [w for w in sc.list_host.findChildren(ClickLabel) if w.text() == "✕"]
    assert len(xs) == 1


def test_chat_delete_open_thread_resets_and_reloads(qtbot):
    from lumen.ui_v2.screens.chat import ChatScreen
    data, chat = FakeClient(), FakeClient()
    sc = ChatScreen(AppState(data=data), chat_client=chat)
    qtbot.addWidget(sc)
    data.cb_for("conversations.list")([{"id": 3, "title": "q", "updated_at": "x"}])
    sc.load_conversation(3)
    data.requests.clear()
    sc.delete_conversation(3)
    t, p, cb = data.requests[0]
    assert (t, p) == ("conversations.delete", {"id": 3})
    cb({"ok": True})
    assert sc._conv_id is None     # the open thread is gone -> blank pane
    assert any(t == "conversations.list" for t, _p, _cb in data.requests)


def test_chat_delete_other_thread_keeps_current(qtbot):
    from lumen.ui_v2.screens.chat import ChatScreen
    data = FakeClient()
    sc = ChatScreen(AppState(data=data), chat_client=FakeClient())
    qtbot.addWidget(sc)
    sc._conv_id = 9
    sc.delete_conversation(3)
    data.cb_for("conversations.delete")({"ok": True})
    assert sc._conv_id == 9


def test_window_registers_chat_and_open_chat_loads(qtbot):
    from lumen.ui_v2.main import LumenWindow, TABS
    data, chat = FakeClient(), FakeClient()
    win = LumenWindow(AppState(data=data, chat=chat))
    qtbot.addWidget(win)
    assert "chat" in TABS and "chat" in win.screens
    win._open_chat(11)
    assert win.stack.currentWidget() is win.screens["chat"]
    assert win.screens["chat"]._conv_id == 11


# ---- mail (live) -----------------------------------------------------------

def daemon_mail_row(i=1, unread=True):
    return {"id": f"m{i}", "sender": "Ada Lovelace <ada@x.com>", "recipients": "me",
            "subject": f"Subject {i}", "snippet": "the analytical…", "body": "full body",
            "labels": ["INBOX", "UNREAD"] if unread else ["INBOX"],
            "received_at": "2026-07-12T10:00:00+00:00", "is_read": not unread,
            "attachments": [], "thread_id": "t"}


def test_refresh_mails_normalizes_and_signals(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    hits = []
    st.mails_changed.connect(lambda: hits.append(1))
    st.refresh_mails()
    data.cb_for("emails.list")({"emails": [daemon_mail_row()], "connected": True,
                                "syncing": False, "last_sync": "x",
                                "counts": {"total": 12, "unread": 1}})
    assert hits and st.mails[0]["from"] == "Ada Lovelace"
    assert st.mails[0]["unread"] is True and st.mail_total == 12
    assert st.mail_connected is True


def test_search_mails_routes_query(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    st.search_mails("budget")
    assert data.requests[-1][0] == "emails.search"
    st.search_mails("")
    assert data.requests[-1][0] == "emails.list"


def test_search_results_do_not_clobber_connection_status(qtbot):
    # emails.search responses carry only {"emails": [...]} — no status keys —
    # so _set_mails must preserve prior connection/sync/total state rather
    # than resetting to its defaults.
    data = FakeClient()
    st = AppState(data=data)
    st.mail_connected = False
    st.mail_total = 42
    st.search_mails("budget")
    data.cb_for("emails.search")({"emails": [daemon_mail_row()]})
    assert st.mail_connected is False
    assert st.mail_total == 42
    assert st.mails[0]["id"] == "m1"


def test_archive_result_toasts_and_refreshes(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    toasts = []
    st.toast_requested.connect(toasts.append)
    st.archive_mail("m1")
    data.cb_for("emails.archive")({"ok": True, "message": "Archived."})
    assert toasts == ["✓ Archived."]
    assert data.requests[-1][0] == "emails.list"      # refresh after action


def test_select_mail_no_longer_marks_read_locally(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    st.refresh_mails()
    data.cb_for("emails.list")({"emails": [daemon_mail_row()], "connected": True,
                                "syncing": False, "last_sync": None,
                                "counts": {"total": 1, "unread": 1}})
    st.select_mail("m1")
    assert st.mails[0]["unread"] is True             # decided gate: no silent flip


def test_mail_search_box_debounces_into_state(qtbot, monkeypatch):
    from lumen.ui_v2.screens.mail import MailScreen
    state = AppState()          # sample mode
    calls = []
    monkeypatch.setattr(state, "search_mails", lambda q: calls.append(q))
    screen = MailScreen(state)
    qtbot.addWidget(screen)
    screen.search_box.setText("budget")
    qtbot.wait(400)             # past the 300ms debounce
    assert calls == ["budget"]


def test_mail_action_buttons_call_state(qtbot, monkeypatch):
    from lumen.ui_v2.screens.mail import MailScreen
    state = AppState()
    archived, marked = [], []
    monkeypatch.setattr(state, "archive_mail", archived.append)
    monkeypatch.setattr(state, "set_mail_read", lambda i, r: marked.append((i, r)))
    screen = MailScreen(state)
    qtbot.addWidget(screen)
    screen.archive_btn.click()
    screen.read_btn.click()
    sel = state.sel_mail()["id"]
    assert archived == [sel] and marked and marked[0][0] == sel


def test_mail_not_connected_and_empty_states(qtbot):
    from lumen.ui_v2.screens.mail import MailScreen
    state = AppState()
    state.mails, state.selected_mail = [], None
    state.mail_connected = False
    screen = MailScreen(state)
    qtbot.addWidget(screen)
    assert "not connected" in screen.status_lab.text().lower()


def test_mail_empty_pane_shows_no_message_selected(qtbot):
    from lumen.ui_v2.screens.mail import MailScreen
    state = AppState()
    state.mails, state.selected_mail = [], None
    screen = MailScreen(state)
    qtbot.addWidget(screen)
    assert "No message selected" in _label_texts(screen)


def test_sample_window_builds_every_screen(qtbot):
    """Construct the full sample-mode window: every screen's layout builds."""
    from lumen.ui_v2.main import LumenWindow, TABS
    from PyQt6.QtWidgets import QWidget
    win = LumenWindow(AppState())
    qtbot.addWidget(win)
    assert set(win.screens) == set(TABS)
    for key in TABS:
        assert isinstance(win.screens[key], QWidget)
        win.switch_to(key)
    assert win.stack.currentWidget() is win.screens["settings"]


# ---- Phase 7: compose plumbing --------------------------------------------

def test_norm_mail_carries_sender_address():
    row = {"id": "x", "sender": "Ada L <a@x.com>", "subject": "s", "snippet": "",
           "received_at": "2026-07-10T10:00:00+00:00", "is_read": True}
    assert _norm_mail(row)["from_addr"] == "a@x.com"
    row["sender"] = "bare@x.com"
    assert _norm_mail(row)["from_addr"] == "bare@x.com"
    row["sender"] = "Just A Name"
    assert _norm_mail(row)["from_addr"] == ""


def test_appstate_compose_senders_route_to_daemon():
    data, confirm = FakeClient(), FakeClient()
    st = AppState(data=data, confirm=confirm)
    st.send_email({"to": ["a@x.com"]}, lambda r: None)
    assert data.requests[-1][0] == "emails.send"
    st.revise_email({"subject": "s"}, lambda r: None)
    assert data.requests[-1][0] == "emails.revise"
    st.respond_compose(7, {"to": []})
    t, p, _cb = confirm.requests[-1]
    assert t == "compose.response" and p["compose_id"] == 7 and p["send"] is True
    st.respond_compose(7, None)
    assert confirm.requests[-1][1]["send"] is False


def test_appstate_open_compose_and_attached_source_emit_signal(qtbot):
    chat = FakeClient()
    st = AppState(data=FakeClient(), chat=chat)
    got = []
    st.compose_requested.connect(got.append)
    st.open_compose({"subject": "s"})
    chat.compose_requested.emit({"compose_id": 3, "to": ["a@x.com"]})
    assert got[0]["subject"] == "s" and got[1]["compose_id"] == 3


def test_appstate_sample_mode_send_email_answers_ok():
    st = AppState()
    got = []
    st.send_email({"to": ["a@x.com"]}, got.append)
    assert got and got[0]["ok"] is True


# ---- compose dialog --------------------------------------------------------

def _compose_host(qtbot, data=None, confirm=None):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QWidget
    from lumen.ui_v2.compose import ComposeDialog
    st = AppState(data=data or FakeClient(), confirm=confirm or FakeClient())
    host = QWidget()
    qtbot.addWidget(host)
    # visible parent (a child's isVisible() is False under a hidden one), but
    # never a real exposure — paint events for GC'd widgets segfault PyQt
    host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    host.show()
    dlg = ComposeDialog(host, st)
    dlg._test_host = host    # qtbot holds only a weakref; keep the parent alive
    return dlg, st


def test_compose_dialog_prefills_and_sends_oneshot(qtbot):
    data = FakeClient()
    dlg, st = _compose_host(qtbot, data=data)
    dlg.open({"to": ["a@x.com"], "subject": "Hi", "body": "B", "reply_to": "m9"})
    assert dlg.to_edit.text() == "a@x.com" and dlg.subject_edit.text() == "Hi"
    dlg.cc_edit.setText("c@x.com, d@x.com")
    dlg._send()
    t, p, cb = data.requests[-1]
    assert t == "emails.send" and p["cc"] == ["c@x.com", "d@x.com"]
    assert p["reply_to"] == "m9" and p["body"] == "B"
    cb({"ok": False, "message": "Couldn't reach Gmail — nothing was sent."})
    assert dlg.isVisible()                      # failure keeps the draft open
    assert "Gmail" in dlg.err_lab.text()
    dlg._send()                                  # resend after the error
    data.requests[-1][2]({"ok": True, "message": "Sent."})
    assert not dlg.isVisible()


def test_compose_dialog_chat_mode_resolves_broker_not_oneshot(qtbot):
    data, confirm = FakeClient(), FakeClient()
    dlg, st = _compose_host(qtbot, data=data, confirm=confirm)
    dlg.open({"compose_id": 5, "to": ["a@x.com"], "body": "b"})
    dlg._send()
    assert not any(t == "emails.send" for t, _p, _cb in data.requests)
    assert confirm.requests[-1][0] == "compose.response"
    assert confirm.requests[-1][1]["send"] is True
    assert not dlg.isVisible()                  # outcome lands in the chat turn
    dlg.open({"compose_id": 6})
    dlg._cancel()
    assert confirm.requests[-1][1] == {"compose_id": 6, "send": False, "fields": {}}
    assert not dlg.isVisible()


def test_compose_dialog_revise_refreshes_draft(qtbot):
    data = FakeClient()
    dlg, st = _compose_host(qtbot, data=data)
    dlg.open({"subject": "Long", "body": "Long body", "to": ["a@x.com"]})
    dlg.revise_edit.setText("shorter")
    dlg._revise()
    t, p, cb = data.requests[-1]
    assert t == "emails.revise" and p["instruction"] == "shorter"
    assert not dlg.revise_btn.isEnabled()       # busy while the model works
    cb({"subject": "Short", "body": "B."})
    assert dlg.subject_edit.text() == "Short" and dlg.body_edit.toPlainText() == "B."
    assert dlg.to_edit.text() == "a@x.com"      # recipients never touched
    assert dlg.revise_btn.isEnabled() and dlg.revise_edit.text() == ""


def test_compose_dialog_revise_unblocks_on_daemon_error(qtbot):
    dlg, st = _compose_host(qtbot)
    dlg.open({"body": "b"})
    dlg.revise_edit.setText("shorter")
    dlg._revise()
    assert not dlg.revise_btn.isEnabled()
    st.status_requested.emit("daemon offline")  # callback will never fire
    assert dlg.revise_btn.isEnabled()


def test_window_opens_compose_on_state_signal(qtbot):
    from lumen.ui_v2.main import LumenWindow
    st = AppState(data=FakeClient(), chat=FakeClient(), confirm=FakeClient())
    win = LumenWindow(st)
    qtbot.addWidget(win)
    # _open_compose surfaces the window; never map it for real in tests —
    # spinning the event loop delivers ghost paints to GC'd earlier widgets
    win.show = lambda: None
    win.state.compose_requested.emit({"subject": "s"})
    assert not win.compose.isHidden() and win.compose.subject_edit.text() == "s"


def test_mail_screen_compose_button_opens_empty_popup(qtbot):
    from lumen.ui_v2.screens.mail import MailScreen
    st = AppState()
    got = []
    st.compose_requested.connect(got.append)
    sc = MailScreen(st)
    qtbot.addWidget(sc)
    sc.compose_btn.click()
    assert got == [{}]


def test_mail_screen_reply_prefills_sender_and_threading(qtbot):
    from lumen.ui_v2.screens.mail import MailScreen
    st = AppState()
    st.mails[0].update({"from_addr": "priya@x.com", "subj": "Budget"})
    st.selected_mail = st.mails[0]["id"]
    got = []
    st.compose_requested.connect(got.append)
    sc = MailScreen(st)
    qtbot.addWidget(sc)
    sc.reply_btn.click()
    assert got[0]["to"] == ["priya@x.com"]
    assert got[0]["subject"] == "Re: Budget"
    assert got[0]["reply_to"] == st.mails[0]["id"]


def test_dashboard_manabi_nudge_row(qtbot):
    from lumen.ui_v2.screens.dashboard import DashboardScreen
    from PyQt6.QtWidgets import QLabel
    data = FakeClient()
    st = AppState(data=data)
    sc = DashboardScreen(st)
    qtbot.addWidget(sc)

    def texts():
        return [l.text() for l in sc.todo_col.findChildren(QLabel)]

    assert not any("Japanese" in t for t in texts())
    st.refresh_manabi()
    cbs = [cb for t, _p, cb in data.requests if t == "manabi.status"]
    cbs[-1]({"configured": True, "due": True, "last_review": None})
    assert st.manabi_due is True
    assert any("Japanese reviews not done" in t for t in texts())
    # doing the reviews clears it on the next fetch
    st.refresh_manabi()
    cbs = [cb for t, _p, cb in data.requests if t == "manabi.status"]
    cbs[-1]({"configured": True, "due": False,
             "last_review": "2026-07-14T08:00:00-06:00"})
    assert not any("Japanese" in t for t in texts())


def test_manabi_sample_mode_never_nudges(qtbot):
    st = AppState()
    st.refresh_manabi()                     # no client — must be a no-op
    assert st.manabi_due is False
