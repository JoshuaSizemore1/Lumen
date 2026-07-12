"""v2 UI wiring: AppState as the live daemon seam, confirm-over-IPC routing,
launcher streaming, and calendar/todo/book shape adaptation.

A FakeClient stands in for DaemonClient — same signals + methods, no socket."""
from PyQt6.QtCore import QObject, pyqtSignal

from lumen.ui_v2 import state as state_mod
from lumen.ui_v2.state import AppState, _norm_book, _norm_event, _norm_todo


class FakeClient(QObject):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)
    tool_used = pyqtSignal(str)
    conversation = pyqtSignal(int)
    confirm_requested = pyqtSignal(dict)

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


# ---- launcher streaming --------------------------------------------------

def test_launcher_palette_streams(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    st = AppState()
    chat = FakeClient()
    pal = LauncherPalette(st, chat)
    qtbot.addWidget(pal)
    pal.input.setText("what's on today")
    pal._submit()
    assert chat.sent[0] == ("chat", {"message": "what's on today"})
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


# ---- launcher multi-turn + handoff ---------------------------------------

def test_launcher_followup_reuses_conversation_id(qtbot):
    from lumen.ui_v2.screens.launcher import LauncherPalette
    chat = FakeClient()
    pal = LauncherPalette(AppState(), chat)
    qtbot.addWidget(pal)
    pal.input.setText("first")
    pal._submit()
    assert chat.sent[0] == ("chat", {"message": "first"})   # first turn: no id yet
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


def test_chat_screen_new_chat_resets(qtbot):
    from lumen.ui_v2.screens.chat import ChatScreen
    sc = ChatScreen(AppState(data=FakeClient()), chat_client=FakeClient())
    qtbot.addWidget(sc)
    sc._conv_id = 9
    sc.input.setText("hi")
    sc._submit()
    sc.new_chat()
    assert sc._conv_id is None and sc.resp_text is None


def test_window_registers_chat_and_open_chat_loads(qtbot):
    from lumen.ui_v2.main import LumenWindow, TABS
    data, chat = FakeClient(), FakeClient()
    win = LumenWindow(AppState(data=data, chat=chat))
    qtbot.addWidget(win)
    assert "chat" in TABS and "chat" in win.screens
    win._open_chat(11)
    assert win.stack.currentWidget() is win.screens["chat"]
    assert win.screens["chat"]._conv_id == 11


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
