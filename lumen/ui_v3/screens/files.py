"""Files — the mockup's card browser plus an editor for text files.

Per the design call, there is no per-screen "ask about this file" input: the
bottom Ask Lumen bar is the single ask surface and carries the open file as
context. What stays here is the one thing the ask bar cannot do — ✎ Edit, which
proposes a rewrite of the buffer that you apply or discard.
"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QGridLayout, QLineEdit, QPlainTextEdit, QWidget

from .. import theme as T
from ..widgets import (
    ClickLabel, ClickRow, ElideLabel, TypingDots, button, clear_layout,
    empty_state, eyebrow, font, hbox, hline, label, qcolor, scroll, vbox,
)

TEXT_SUFFIXES = {".md", ".txt", ".toml", ".conf", ".cfg", ".ini", ".json",
                 ".jsonc", ".py", ".sh", ".yaml", ".yml", ".rs", ".js", ".ts",
                 ".css", ".html", ".xml", ".log", ".env"}
CARD_MIN_W = 150
CARD_GAP = 12


class _CardGrid(QWidget):
    """Reflowing card grid: column count follows the available width, matching
    the mock's `repeat(auto-fill, minmax(150px, 1fr))`."""

    def __init__(self, entries: list[dict], make_card):
        super().__init__()
        self._cards = [make_card(e) for e in entries]
        self._cols = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(30, 22, 30, 22)
        self.grid.setSpacing(CARD_GAP)
        self._relayout(5)

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
        usable = self.width() - 60      # the grid's own 30px side margins
        cols = max(1, (usable + CARD_GAP) // (CARD_MIN_W + CARD_GAP))
        self._relayout(int(cols))


class FilesScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self.path = Path.home()
        self.open_file: Path | None = None
        self.dirty = False
        self._proposal: str | None = None

        root = vbox(self, (0, 0, 0, 0), 0)
        self.crumb_host = QWidget()
        self.crumb_lay = hbox(self.crumb_host, (30, 18, 30, 14), 12)
        root.addWidget(self.crumb_host)
        root.addWidget(hline(T.BORDER_MED))

        self.body = QWidget()
        self.body_lay = vbox(self.body, (0, 0, 0, 0), 0)
        root.addWidget(self.body, 1)
        self.rebuild()

    # ---- navigation -------------------------------------------------------
    def _open(self, p: Path):
        changed = self._go(p)
        self.rebuild()
        # Every place the Files screen settles — a folder step or opening a
        # text file — is a location on the app-wide back/forward history (#29),
        # exactly like the calendar's in-page steps.
        if changed:
            self.state.nav_location_changed.emit()

    def _go(self, p: Path) -> bool:
        """Move to `p` (a dir to browse or a text file to open). Returns True if
        the location actually changed, so `_open` only records real steps."""
        if p.is_dir():
            if self.path == p and self.open_file is None:
                return False
            self.path = p
            self.open_file = None
            self.dirty = False
            return True
        if p.suffix.lower() in TEXT_SUFFIXES:
            result = self.state.read_file(p)
            if result.get("error"):
                self.state.toast_requested.emit(f"⚠ {result['error']}")
                return False
            self.open_file = p
            self.editor_text = result.get("content", "")
            self.dirty = False
            return True
        return False

    def open_path(self, p) -> None:
        """Open a file requested from elsewhere in the shell, e.g. Settings'
        'open memory.md' (#15). Land in the file's directory so Close returns
        there rather than home."""
        p = Path(p).expanduser()
        if p.parent.is_dir():
            self.path = p.parent
        self._open(p)

    def context(self) -> dict:
        """What the ask bar should carry when this screen is showing."""
        if self.open_file is not None:
            return {"screen": "files", "file": str(self.open_file),
                    "content": self.editor.toPlainText()[:8000]}
        return {"screen": "files", "dir": str(self.path)}

    # ---- app-wide back/forward hook (#29) ---------------------------------
    # Mirrors the calendar: the shell's SwipeNavigator owns the gesture and the
    # unified history; Files just exposes its in-page location (which folder,
    # which open file) so back/forward can restore it.
    def nav_token(self) -> tuple:
        return (self.path, self.open_file)

    def nav_restore(self, token: tuple) -> None:
        # The shell replaying history, not a fresh navigation — apply the
        # location WITHOUT emitting nav_location_changed.
        path, open_file = token
        self.path = path
        self.dirty = False
        if open_file is None:
            self.open_file = None
        else:
            result = self.state.read_file(open_file)
            if result.get("error"):
                # Vanished/unreadable since it was visited — fall back to the
                # folder rather than restoring a broken editor.
                self.open_file = None
            else:
                self.open_file = open_file
                self.editor_text = result.get("content", "")
        self.rebuild()

    # ---- build ------------------------------------------------------------
    def rebuild(self):
        clear_layout(self.crumb_lay)
        clear_layout(self.body_lay)
        self._crumbs()
        if self.open_file is None:
            self._browser()
        else:
            self._editor()

    def _crumbs(self):
        # "Up a directory" — the clicking-a-crumb-ancestor path always existed,
        # but there was no one-click way up one level (#25). Browser-only: while
        # editing, Close is the way back out.
        if self.open_file is None:
            at_root = self.path.parent == self.path
            up = button("↑ Up", "soft", px=11.5, height=26)
            up.setToolTip("Go up to the parent folder")
            up.setEnabled(not at_root)
            if not at_root:
                up.clicked.connect(lambda: self._open(self.path.parent))
            self.crumb_lay.addWidget(up)

        parts = list(self.path.parts)
        home = Path.home().parts
        # Render the home prefix as ~, the way the mock does.
        if tuple(parts[:len(home)]) == home:
            crumbs, acc, rest = [("~", Path.home())], Path.home(), parts[len(home):]
        else:
            crumbs, acc, rest = [(parts[0], Path(parts[0]))], Path(parts[0]), parts[1:]
        for p in rest:
            acc = acc / p
            crumbs.append((p, acc))

        row = hbox(s=4)
        for i, (name, target) in enumerate(crumbs):
            last = i == len(crumbs) - 1
            text = name if i == 0 else f"/ {name}"
            row.addWidget(ClickLabel(
                text, 13, T.TEXT_PRIMARY if last else T.ACCENT, mono=True,
                weight=700 if last else 400,
                on_click=None if last else (lambda t=target: self._open(t))))
        self.crumb_lay.addLayout(row)
        self.crumb_lay.addStretch(1)
        self.crumb_lay.addWidget(label(self._count(), 10, T.TEXT_FAINT,
                                       mono=True))

    def _count(self) -> str:
        entries = self._entries()
        dirs = sum(1 for e in entries if e["dir"])
        return f"{dirs} folders · {len(entries) - dirs} files"

    def _entries(self) -> list[dict]:
        """local_files.list_dir already sorts folders-first, case-insensitive,
        and reports errors as data rather than raising."""
        result = self.state.list_dir(self.path)
        self._error = result.get("error")
        return [{"name": e["name"], "dir": e["is_dir"], "size": e["size"]}
                for e in result.get("entries", [])]

    def _browser(self):
        entries = self._entries()
        if self._error:
            self.body_lay.addWidget(empty_state("Can't open this folder",
                                                self._error))
            return
        if not entries:
            self.body_lay.addWidget(empty_state("This folder is empty"))
            return
        # The mock's grid is repeat(auto-fill, minmax(150px, 1fr)) — the column
        # count is a function of width, so it is recomputed on resize rather
        # than frozen at whatever fits the design size.
        self.grid_host = _CardGrid(entries, self._card)
        self.body_lay.addWidget(scroll(self.grid_host), 1)

        foot = hbox(m=(30, 9, 30, 9), s=18)
        foot.addWidget(label("click a text file to edit it", 10, T.TEXT_FAINT,
                             mono=True))
        foot.addWidget(label("indexed locally · never uploaded", 10,
                             T.TEXT_FAINT, mono=True))
        foot.addStretch(1)
        self.body_lay.addWidget(hline(T.BORDER_MED))
        self.body_lay.addLayout(foot)

    def _card(self, e: dict) -> QWidget:
        target = self.path / e["name"]
        editable = e["dir"] or target.suffix.lower() in TEXT_SUFFIXES
        card = ClickRow((lambda t=target: self._open(t)) if editable else None)
        card.setProperty("role", "folder" if e["dir"] else "card")
        card.setMinimumWidth(CARD_MIN_W)
        v = vbox(card, (10, 18, 10, 14), 6)
        # 15px, not the mock's 20: IBM Plex Mono draws these glyphs much
        # heavier than Space Mono does, and 20 reads as a solid block.
        icon = label("▸" if e["dir"] else ("≡" if editable else "·"), 15,
                     T.ACCENT if e["dir"] else (T.WARN if editable
                                                else T.TEXT_FAINTER),
                     mono=True)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(icon)
        # Elided, not wrapped: a long filename must not stretch its card or
        # spill past it (the mock caps names at 100% with an ellipsis).
        name = ElideLabel(e["name"], 12, T.TEXT_PRIMARY,
                          700 if e["dir"] else 400, mono=True,
                          align=Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(name)
        sub = "folder" if e["dir"] else self._size(e["size"])
        sublab = label(sub, 9.5, T.TEXT_FAINT, mono=True)
        sublab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(sublab)
        return card

    @staticmethod
    def _size(n) -> str:
        if not isinstance(n, (int, float)):
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    # ---- editor -----------------------------------------------------------
    def _editor(self):
        bar = QWidget()
        row = hbox(bar, (30, 11, 30, 11), 10)
        row.addWidget(label(self.open_file.name, 13, T.TEXT_PRIMARY, 700,
                            mono=True))
        self.dirty_lab = label("saved · local disk", 10, T.OK, mono=True)
        row.addWidget(self.dirty_lab)
        row.addStretch(1)

        self.edit_status = TypingDots("", 10, T.TEXT_FAINT)
        row.addWidget(self.edit_status)
        edit_b = button("✎ Edit", "soft", px=12.5, height=28)
        edit_b.setToolTip("Describe a change; Lumen proposes a rewrite you "
                          "apply or discard")
        edit_b.clicked.connect(self._ask_edit)
        row.addWidget(edit_b)
        save = button("Save", "primary", px=12.5, height=28)
        save.clicked.connect(self._save)
        row.addWidget(save)
        close = button("Close", "ghost", px=12.5, height=28)
        close.clicked.connect(self._close)
        row.addWidget(close)
        self.body_lay.addWidget(bar)
        self.body_lay.addWidget(hline(T.BORDER_FAINT))

        # instruction row, revealed by ✎ Edit
        self.instr_host = QFrame()
        self.instr_host.setProperty("role", "panel")
        ih = hbox(self.instr_host, (12, 8, 12, 8), 9)
        ih.addWidget(label("❯", 12, T.ACCENT, mono=True))
        self.instr = QLineEdit()
        self.instr.setProperty("cls", "bare")
        self.instr.setPlaceholderText(
            "Describe the edit — “add a docstring”, “convert to bullets”…")
        self.instr.setFont(font(13))
        self.instr.returnPressed.connect(self._run_edit)
        ih.addWidget(self.instr, 1)
        self.instr_host.hide()
        wrap = hbox(m=(30, 8, 30, 0), s=0)
        wrap.addWidget(self.instr_host)
        self.body_lay.addLayout(wrap)

        # proposal accept/discard bar
        self.prop_host = QFrame()
        self.prop_host.setProperty("role", "ok")
        ph = hbox(self.prop_host, (12, 8, 12, 8), 9)
        ph.addWidget(label("Lumen proposed a rewrite of this file.", 13,
                           T.TEXT_BODY), 1)
        apply_b = button("Apply", "primary", px=12, height=26)
        apply_b.clicked.connect(self._apply_proposal)
        ph.addWidget(apply_b)
        drop_b = button("Discard", "ghost", px=12, height=26)
        drop_b.clicked.connect(self._discard_proposal)
        ph.addWidget(drop_b)
        self.prop_host.hide()
        pwrap = hbox(m=(30, 8, 30, 0), s=0)
        pwrap.addWidget(self.prop_host)
        self.body_lay.addLayout(pwrap)

        self.editor = QPlainTextEdit()
        self.editor.setProperty("cls", "editor")
        self.editor.setPlainText(getattr(self, "editor_text", ""))
        self.editor.textChanged.connect(self._on_dirty)
        self.body_lay.addWidget(self.editor, 1)

    def _on_dirty(self):
        if not self.dirty:
            self.dirty = True
            self.dirty_lab.setText("● unsaved changes")
            pal = self.dirty_lab.palette()

            pal.setColor(self.dirty_lab.foregroundRole(), qcolor(T.WARN))
            self.dirty_lab.setPalette(pal)

    def _save(self):
        result = self.state.save_file(self.open_file, self.editor.toPlainText())
        if result.get("error"):
            self.state.toast_requested.emit(f"⚠ {result['error']}")
            return
        self.dirty = False
        self.dirty_lab.setText("saved · local disk")

        pal = self.dirty_lab.palette()
        pal.setColor(self.dirty_lab.foregroundRole(), qcolor(T.OK))
        self.dirty_lab.setPalette(pal)
        self.state.toast_requested.emit(f"✓ Saved {self.open_file.name}")

    def _close(self):
        self.open_file = None
        self.dirty = False
        self._proposal = None
        self.rebuild()
        # Closing returns to the folder — a new location on the history (#29).
        self.state.nav_location_changed.emit()

    # ---- assisted edit ----------------------------------------------------
    def _ask_edit(self):
        self.instr_host.show()
        self.instr.setFocus()

    def _run_edit(self):
        instruction = self.instr.text().strip()
        if not instruction:
            return
        self.instr.clear()
        self.instr_host.hide()
        self.edit_status.start("◇ drafting")

        def done(result):
            self.edit_status.set_static("")
            if not (result or {}).get("ok"):
                self.state.toast_requested.emit(
                    f"⚠ {(result or {}).get('message', 'Edit failed')}")
                return
            self._proposal = result.get("content", "")
            self.prop_host.show()

        self.state.propose_edit(str(self.open_file),
                                self.editor.toPlainText(), instruction, done)

    def _apply_proposal(self):
        if self._proposal is not None:
            self.editor.setPlainText(self._proposal)
        self._proposal = None
        self.prop_host.hide()

    def _discard_proposal(self):
        self._proposal = None
        self.prop_host.hide()
