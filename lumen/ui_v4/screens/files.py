"""Files: browse folders on this computer, open a text file, edit and save it,
and ask Lumen to propose a rewrite you apply or discard.

Same data flow as ui_v3's Files screen:
  - `state.list_dir(path)` / `state.read_file(path)` / `state.save_file(...)`
    are UI-local (the filesystem is native to this process; a manual Save is
    direct manipulation, like adding a todo).
  - "Suggest an edit" goes to the daemon through `state.propose_edit`; nothing
    is written until you press Apply and then Save.
  - The Ask bar carries the open folder or file as context (`ask_context`), so
    the daemon grounds file questions in what you're looking at.
  - Every folder step, opened file and Close is a stop on the app-wide
    back/forward history (`nav_token` / `nav_restore` +
    `state.nav_location_changed`).
  - `open_path(path)` opens a file requested from elsewhere (Settings' memory
    file, `state.open_file_requested`).
"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QBoxLayout, QFrame, QGridLayout, QPlainTextEdit, QSizePolicy, QWidget,
)

from .. import theme as T
from ..components import (
    Badge, Button, ElideLabel, EmptyState, FlowLayout, IconLabel, Label,
    ModelOffNotice, Panel, ScreenHeader, ScrollArea, TextField, TypingDots,
    clear_layout, fire_on_next_tick, hbox, vbox,
)

TEXT_SUFFIXES = {".md", ".txt", ".toml", ".conf", ".cfg", ".ini", ".json",
                 ".jsonc", ".py", ".sh", ".yaml", ".yml", ".rs", ".js", ".ts",
                 ".css", ".html", ".xml", ".log", ".env"}
# Prose files read better in the body face; everything else is code.
PROSE_SUFFIXES = {".md", ".txt", ".log"}
CARD_MIN_W = 168
CARD_GAP = T.S3
NARROW = 720          # content width below which toolbars stack


def _size(n) -> str:
    if not isinstance(n, (int, float)):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _pretty(p: Path) -> str:
    """A path with the home prefix shown as ~."""
    home = Path.home()
    try:
        rel = p.relative_to(home)
    except ValueError:
        return str(p)
    return "~" if str(rel) == "." else f"~/{rel}"


class _CardGrid(QWidget):
    """Reflowing card grid: the column count follows the available width
    (ui_v3's `repeat(auto-fill, minmax(150px, 1fr))`)."""

    def __init__(self, cards: list[QWidget]):
        super().__init__()
        self._cards = cards
        self._cols = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(CARD_GAP)
        self._relayout(4)

    def _relayout(self, cols: int):
        if cols == self._cols:
            return
        self._cols = cols
        for card in self._cards:
            self.grid.removeWidget(card)
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for i, card in enumerate(self._cards):
            self.grid.addWidget(card, i // cols, i % cols)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        cols = max(1, (self.width() + CARD_GAP) // (CARD_MIN_W + CARD_GAP))
        self._relayout(int(cols))


class _FileCard(QFrame):
    """One folder or file. Folders and text files are clickable; anything
    else is shown but inert, with a tooltip saying why."""

    def __init__(self, entry: dict, target: Path, openable: bool, on_open):
        super().__init__()
        self.setProperty("role", "row")
        self.setProperty("selected", False)
        self.setMinimumWidth(CARD_MIN_W)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self._on_open = on_open if openable else None
        is_dir = entry["dir"]
        kind = "Folder" if is_dir else ("Text file" if openable else "File")
        self.setAccessibleName(f"{kind}: {entry['name']}")
        if self._on_open is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.setToolTip(f"Open {entry['name']}")
        else:
            self.setToolTip("Lumen can only open folders and text files.")

        v = vbox(self, (T.S3, T.S4, T.S3, T.S3), T.S2)
        icon = "files" if is_dir else "file"
        color = "accent" if is_dir else ("fg2" if openable else "muted")
        v.addWidget(IconLabel(icon, color, 24), 0, Qt.AlignmentFlag.AlignLeft)
        name = ElideLabel(entry["name"], "body")
        v.addWidget(name)
        if is_dir:
            v.addWidget(Label("Folder", "muted"))
        else:
            sz = _size(entry.get("size"))
            v.addWidget(Label(sz or kind, "meta" if sz else "muted"))

    def _fire(self):
        if self._on_open is not None:
            fire_on_next_tick(self._on_open)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._fire()
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if self._on_open and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                          Qt.Key.Key_Space):
            self._fire()
        else:
            super().keyPressEvent(ev)


class FilesScreen(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self.path = Path.home()
        self.open_file: Path | None = None
        self.editor_text = ""
        self.dirty = False
        self._proposal: str | None = None
        self._error: str | None = None
        self._narrow = False
        self.editor: QPlainTextEdit | None = None
        try:
            from .settings import install_text_size
            install_text_size()
        except Exception:
            pass

        root = vbox(self, (T.S8, T.S6, T.S8, T.S6), T.S5)
        self.header = ScreenHeader(
            "Files", "Browse and edit text files on this computer. Nothing "
                     "here is uploaded.")
        root.addWidget(self.header)

        self.body = QWidget()
        self.body_lay = vbox(self.body, (0, 0, 0, 0), T.S4)
        root.addWidget(self.body, 1)
        self.rebuild()

    # ---- window hooks -------------------------------------------------------
    def on_shown(self):
        # Re-read the folder every visit: files change underneath us. An open
        # editor is left alone so unsaved text is never thrown away.
        if self.open_file is None:
            self.rebuild()

    def refresh(self):
        if self.open_file is None:
            self.rebuild()

    def ask_context(self) -> dict:
        """Dict, not a string: the daemon reads `screen` + `file`/`dir` from it
        to ground the answer in this folder or file (router._files_grounding)."""
        if self.open_file is not None and self.editor is not None:
            return {"screen": "files", "file": str(self.open_file),
                    "content": self.editor.toPlainText()[:8000]}
        return {"screen": "files", "dir": str(self.path)}

    def nav_token(self) -> tuple:
        return (self.path, self.open_file)

    def nav_restore(self, token) -> None:
        """History replay: apply the location without recording a new step."""
        try:
            path, open_file = token
        except (TypeError, ValueError):
            return
        self.path = Path(path)
        self.dirty = False
        self._proposal = None
        if open_file is None:
            self.open_file = None
        else:
            result = self.state.read_file(open_file)
            if result.get("error"):
                self.open_file = None      # vanished since; show its folder
            else:
                self.open_file = Path(open_file)
                self.editor_text = result.get("content", "")
        self.rebuild()

    def open_path(self, p) -> None:
        """Open a file (or folder) requested from elsewhere in the app. Lands
        in the file's folder so Close returns there rather than home."""
        p = Path(p).expanduser()
        if not p.is_dir() and p.parent.is_dir():
            self.path = p.parent
        self._open(p)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() < NARROW
        if narrow != self._narrow:
            self._narrow = narrow
            self._apply_narrow()

    # ---- navigation ---------------------------------------------------------
    def _open(self, p: Path):
        changed = self._go(p)
        self.rebuild()
        if changed:
            self._emit_location()

    def _emit_location(self):
        sig = getattr(self.state, "nav_location_changed", None)
        if sig is not None:
            sig.emit()

    def _go(self, p: Path) -> bool:
        """Move to `p` (a folder to browse or a text file to open). True if the
        location actually changed."""
        if p.is_dir():
            if self.path == p and self.open_file is None:
                return False
            self.path = p
            self.open_file = None
            self.dirty = False
            self._proposal = None
            return True
        if p.suffix.lower() in TEXT_SUFFIXES:
            result = self.state.read_file(p)
            if result.get("error"):
                self.win.show_toast(f"Couldn't open {p.name}: {result['error']}")
                return False
            self.open_file = p
            self.editor_text = result.get("content", "")
            self.dirty = False
            self._proposal = None
            return True
        self.win.show_toast(f"Lumen can only open text files, not {p.name}.")
        return False

    # ---- build --------------------------------------------------------------
    def rebuild(self):
        clear_layout(self.header.actions)
        clear_layout(self.body_lay)
        self.editor = None
        self._tool_row = None
        if self.open_file is None:
            self._browser()
        else:
            self._editor()
        self._apply_narrow()

    def _apply_narrow(self):
        row = getattr(self, "_tool_row", None)
        if row is not None:
            row.setDirection(QBoxLayout.Direction.TopToBottom if self._narrow
                             else QBoxLayout.Direction.LeftToRight)

    # ---- browser ------------------------------------------------------------
    def _entries(self) -> list[dict]:
        """local_files.list_dir sorts folders first, case-insensitively, and
        reports errors as data rather than raising."""
        result = self.state.list_dir(self.path)
        self._error = result.get("error")
        return [{"name": e["name"], "dir": e["is_dir"], "size": e.get("size")}
                for e in result.get("entries", [])]

    def _browser(self):
        self.header.set_title("Files")
        self.header.set_subtitle("Browse and edit text files on this computer. "
                                 "Nothing here is uploaded.")
        at_root = self.path.parent == self.path
        up = Button("Up one folder", "secondary", icon="arrow-up",
                    on_click=lambda: self._open(self.path.parent))
        up.setToolTip("Go to the parent folder")
        up.setEnabled(not at_root)
        self.header.add_action(up)

        entries = self._entries()

        # Breadcrumbs + counts. The crumbs wrap (FlowLayout) when narrow.
        bar = QWidget()
        bl = hbox(bar, (0, 0, 0, 0), T.S3)
        crumbs = QWidget()
        flow = FlowLayout(crumbs, hgap=T.S1, vgap=T.S1)
        for i, (name, target, last) in enumerate(self._crumbs()):
            if i:
                flow.addWidget(Label("/", "muted"))
            if last:
                cur = Label(name, "body")
                cur.setAccessibleName(f"Current folder: {name}")
                flow.addWidget(cur)
            else:
                b = Button(name, "ghost", size="sm",
                           on_click=lambda t=target: self._open(t))
                b.setToolTip(f"Go to {_pretty(target)}")
                flow.addWidget(b)
        bl.addWidget(crumbs, 1)
        if not self._error:
            dirs = sum(1 for e in entries if e["dir"])
            files = len(entries) - dirs
            count = Label(f"{dirs} folder{'' if dirs == 1 else 's'} · "
                          f"{files} file{'' if files == 1 else 's'}", "meta")
            bl.addWidget(count, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body_lay.addWidget(bar)

        if self._error:
            self.body_lay.addWidget(EmptyState(
                "alert", "This folder can't be opened",
                f"{self._error}. You may not have permission to read it.",
                "Go to your home folder", lambda: self._open(Path.home())), 1)
            return
        if not entries:
            self.body_lay.addWidget(EmptyState(
                "files", "This folder is empty",
                "There are no files or folders in here yet.",
                "Up one folder", lambda: self._open(self.path.parent)), 1)
            return

        cards = []
        for e in entries:
            target = self.path / e["name"]
            openable = e["dir"] or target.suffix.lower() in TEXT_SUFFIXES
            cards.append(_FileCard(e, target, openable,
                                   lambda t=target: self._open(t)))
        grid = _CardGrid(cards)
        sa = ScrollArea(m=(0, 0, 0, T.S2), s=0)
        sa.lay.addWidget(grid)
        sa.lay.addStretch(1)
        self.body_lay.addWidget(sa, 1)
        self.body_lay.addWidget(Label(
            "Open a text file to edit it. Files stay on this computer.",
            "muted"))

    def _crumbs(self) -> list[tuple[str, Path, bool]]:
        parts = list(self.path.parts)
        home = Path.home().parts
        if tuple(parts[:len(home)]) == home:
            out, acc, rest = [("Home", Path.home())], Path.home(), parts[len(home):]
        else:
            out, acc, rest = [(parts[0], Path(parts[0]))], Path(parts[0]), parts[1:]
        for p in rest:
            acc = acc / p
            out.append((p, acc))
        return [(n, t, i == len(out) - 1) for i, (n, t) in enumerate(out)]

    # ---- editor -------------------------------------------------------------
    def _editor(self):
        f = self.open_file
        self.header.set_title(f.name)
        self.header.set_subtitle(f"In {_pretty(f.parent)}")

        # Toolbar: status on the left, actions on the right; stacks when narrow.
        tool = QWidget()
        self._tool_row = QBoxLayout(QBoxLayout.Direction.LeftToRight, tool)
        self._tool_row.setContentsMargins(0, 0, 0, 0)
        self._tool_row.setSpacing(T.S3)
        left = hbox(s=T.S3)
        self.status_badge = Badge("success", "Saved")
        left.addWidget(self.status_badge)
        self.edit_status = TypingDots("Drafting an edit", "muted")
        self.edit_status.setText("")
        left.addWidget(self.edit_status)
        left.addStretch(1)
        self._tool_row.addLayout(left, 1)
        right = hbox(s=T.S2)
        self.suggest_btn = Button("Suggest an edit", "secondary", icon="edit",
                                  on_click=self._ask_edit)
        self.suggest_btn.setToolTip("Describe a change; Lumen proposes a "
                                    "rewrite you can apply or discard")
        right.addWidget(self.suggest_btn)
        self.save_btn = Button("Save", "primary", icon="save",
                               on_click=self._save)
        self.save_btn.setShortcut("Ctrl+S")
        self.save_btn.setToolTip("Save to disk (Ctrl+S)")
        right.addWidget(self.save_btn)
        right.addWidget(Button("Close", "ghost", icon="x", on_click=self._close))
        self._tool_row.addLayout(right)
        self.body_lay.addWidget(tool)

        # Instruction field, revealed by "Suggest an edit".
        self.instr_host = Panel(padding=T.S4, spacing=T.S3)
        self.instr = TextField(
            "What should change?",
            helper="For example: add a docstring, or turn this into bullets. "
                   "Press Enter to ask.",
            placeholder="Describe the edit")
        self.instr.input.returnPressed.connect(self._run_edit)
        self.instr_host.lay.addWidget(self.instr)
        irow = hbox(s=T.S2)
        irow.addStretch(1)
        irow.addWidget(Button("Cancel", "ghost", size="sm",
                              on_click=self.instr_host.hide))
        irow.addWidget(Button("Ask Lumen", "secondary", icon="send", size="sm",
                              on_click=self._run_edit))
        self.instr_host.lay.addLayout(irow)
        self.instr_host.hide()
        self.body_lay.addWidget(self.instr_host)

        # Same slot: with the model off, say so instead of offering a prompt
        # that could never be answered.
        self.edit_off = ModelOffNotice(
            self.state, "Editing and saving still work. Suggested edits need "
                        "the model.")
        self.edit_off.hide()
        self.body_lay.addWidget(self.edit_off)

        # Proposal: apply or discard.
        self.prop_host = Panel(padding=T.S4, spacing=T.S3)
        ph = hbox(s=T.S3)
        ph.addWidget(IconLabel("edit", "accent"), 0, Qt.AlignmentFlag.AlignTop)
        pcol = vbox(s=2)
        pcol.addWidget(Label("Lumen suggested a rewrite of this file.", "body",
                             wrap=True))
        pcol.addWidget(Label("Apply puts it in the editor. Nothing is saved "
                             "until you press Save.", "muted", wrap=True))
        ph.addLayout(pcol, 1)
        self.prop_host.lay.addLayout(ph)
        prow = hbox(s=T.S2)
        prow.addStretch(1)
        prow.addWidget(Button("Discard", "ghost", size="sm",
                              on_click=self._discard_proposal))
        prow.addWidget(Button("Apply", "secondary", icon="check", size="sm",
                              on_click=self._apply_proposal))
        self.prop_host.lay.addLayout(prow)
        self.prop_host.setVisible(self._proposal is not None)
        self.body_lay.addWidget(self.prop_host)

        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName(f"Contents of {f.name}")
        if f.suffix.lower() not in PROSE_SUFFIXES:
            self.editor.setProperty("role", "code")
        self.editor.setPlainText(self.editor_text)
        self.editor.textChanged.connect(self._on_dirty)
        self.body_lay.addWidget(self.editor, 1)
        self.editor.setFocus()

    def _set_status(self, dirty: bool):
        if dirty:
            self.status_badge.set_kind("warn", "Unsaved changes")
        else:
            self.status_badge.set_kind("success", "Saved")
        self.status_badge.adjustSize()

    def _on_dirty(self):
        if not self.dirty:
            self.dirty = True
            self._set_status(True)

    def _save(self):
        if self.open_file is None or self.editor is None:
            return
        result = self.state.save_file(self.open_file, self.editor.toPlainText())
        if result.get("error"):
            self.win.show_toast(f"Couldn't save {self.open_file.name}: "
                                f"{result['error']}")
            return
        self.dirty = False
        self.editor_text = self.editor.toPlainText()
        self._set_status(False)
        self.win.show_toast(f"Saved {self.open_file.name}")

    def _close(self):
        """Back to the folder. Unsaved text isn't thrown away silently: the
        toast offers Undo, which reopens the file with your edits."""
        lost = None
        if self.dirty and self.editor is not None and self.open_file is not None:
            lost = (self.open_file, self.editor.toPlainText())
        self.open_file = None
        self.dirty = False
        self._proposal = None
        self.rebuild()
        self._emit_location()
        if lost is not None:
            self.win.show_toast(f"Closed {lost[0].name} without saving",
                                undo=lambda l=lost: self._reopen_unsaved(*l))

    def _reopen_unsaved(self, path: Path, text: str):
        if self.open_file is not None:
            return
        self.path = path.parent
        self.open_file = path
        self.editor_text = text
        self._proposal = None
        self.rebuild()
        self.dirty = False
        self._on_dirty()           # it is unsaved again
        self._emit_location()

    # ---- suggested edit -------------------------------------------------------
    def _ask_edit(self):
        if not self.state.model_enabled:
            self.instr_host.hide()
            self.edit_off.show()
            return
        self.edit_off.hide()
        self.instr_host.show()
        self.instr.setFocus()

    def _run_edit(self):
        instruction = self.instr.text().strip()
        if not instruction:
            self.instr.set_error("Say what should change first.")
            return
        if self.open_file is None or self.editor is None:
            return
        self.instr.set_text("")
        self.instr_host.hide()
        self.edit_status.start("Drafting an edit")
        self.suggest_btn.set_busy(True, "Drafting…")
        target = self.open_file

        def done(result):
            try:
                self.edit_status.set_static("")
                self.suggest_btn.set_busy(False)
            except RuntimeError:
                return                   # screen went away mid-request
            if self.open_file != target:
                return                   # a different file is open now
            result = result or {}
            if result.get("model_off"):
                self.state._set_model_enabled(False)
                self.edit_off.show()
                return
            if not result.get("ok"):
                self.win.show_toast(result.get("message") or
                                    "Lumen couldn't draft that edit.")
                return
            self._proposal = result.get("content", "")
            self.prop_host.show()

        self.state.propose_edit(str(target), self.editor.toPlainText(),
                                instruction, done)

    def _apply_proposal(self):
        if self._proposal is not None and self.editor is not None:
            self.editor.setPlainText(self._proposal)
        self._proposal = None
        self.prop_host.hide()

    def _discard_proposal(self):
        self._proposal = None
        self.prop_host.hide()
