"""ui_v3 reuses ui_v2's AppState wholesale.

AppState is the daemon seam — normalization, IPC, sample-mode fallbacks — and
contains no UI, so there is nothing to port and no reason to fork it. Keeping
one copy also means the two shells cannot drift while both exist.

This subclass adds only the signals the Relay layout needs that the old
tab-based shell had no equivalent for. When ui_v2 is retired, move
ui_v2/state.py + ui_v2/sample_data.py here and drop the import.
"""
from PyQt6.QtCore import pyqtSignal

from ..ui_v2.state import AppState as _AppState


class AppState(_AppState):
    # The mockup composes events in a window-level overlay (ui_v2 used a local
    # QDialog owned by the calendar screen), so the screen has to ask the shell.
    event_compose_requested = pyqtSignal(dict)
    # Ask-bar submissions travel to the shell, which owns the answer panel.
    ask_submitted = pyqtSignal(str)
    # Text size, as a percentage. Like the accent, it rebuilds the window: both
    # fonts and layout metrics are read once, at widget construction.
    font_scale_requested = pyqtSignal(int)
    # Open a local file inside the shell's Files screen (path as a string).
    open_file_requested = pyqtSignal(str)
    # A screen navigated within itself (e.g. the calendar changed view/date).
    # The shell records the new location onto the app-wide back/forward history
    # (#18); a plain section switch is caught separately off the stack.
    nav_location_changed = pyqtSignal()

    def open_memory_file(self) -> None:
        """Open memory.md in Lumen's own Files screen (#15).

        ui_v2 shelled out to QDesktopServices.openUrl, which silently no-ops for
        .md on some Linux setups (no registered handler) — Josh saw "nothing".
        Staying in-app also matches the rest of the UI.
        """
        from lumen.daemon.config import default_memory_path
        self.open_file_requested.emit(str(default_memory_path()))
