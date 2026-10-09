"""ui_v4 screens — one module per sidebar destination.

Contract (LumenWindow in ../main.py loads these lazily, on first show):

  key        module                         class
  today      lumen.ui_v4.screens.today      TodayScreen
  mail       lumen.ui_v4.screens.mail       MailScreen      (sidebar: "Inbox")
  calendar   lumen.ui_v4.screens.calendar   CalendarScreen
  todos      lumen.ui_v4.screens.todos      TodosScreen
  books      lumen.ui_v4.screens.books      BooksScreen
  files      lumen.ui_v4.screens.files      FilesScreen
  canvas     lumen.ui_v4.screens.canvas     CanvasScreen
  ask        lumen.ui_v4.screens.ask        AskScreen       (replaces ui_v3 chat)
  settings   lumen.ui_v4.screens.settings   SettingsScreen

  Legacy keys passed to switch_to(): "chat" -> ask, "dashboard" -> today.

Each is `class XScreen(QWidget): def __init__(self, window)`. A failure to
import or construct shows an error state with "Try again", not a crash.

Window API (`window` = LumenWindow):
  .state                  ui_v3 AppState (daemon seam; ui_v2 AppState + signals)
  .chat_client            the chat DaemonClient (None in sample mode)
  .switch_to(key, **kw)   kw reaches on_shown(**kw), filtered to its params
  .show_toast(msg, undo=None)   undo: zero-arg callable -> "Undo" button
  .confirm.open(payload, callback(approved: bool, payload))
  .compose.open(payload)  or state.open_compose(prefill)
  .event.open(payload)    or window.open_event(payload)
  .rules.open(prefill)    or state.open_rule_editor(prefill)
  .theme                  current theme.Theme (tokens); theme.manager().changed
  .ask(question, context=None)  switches to Ask and calls AskScreen.start(...)
  .screen(key)            a built screen or None (never builds)
  .current_key(), .go_back(), .go_forward(), .cycle_theme()

Optional screen hooks (all called through a guard that logs + toasts errors):
  on_shown(**kw)          every switch_to() onto the screen, including first
  ask_context() -> str    page context the Ask bar sends with a question
  refresh()               reload data (screens own when it's called)
  apply_theme()           after a theme switch, for custom-painted colors
  nav_token() / nav_restore(token)   in-page back/forward history
AskScreen additionally: start(question, context) [required for window.ask],
  open_conversation(conv_id) or load_conversation(conv_id).
FilesScreen additionally: open_path(path).
"""
