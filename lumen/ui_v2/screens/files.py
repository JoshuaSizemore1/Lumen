"""Files workbench (new-features items 6-7): 300px browser | editor pane,
with an ask/edit prompt row along the bottom.

Browsing, opening, and the user's own Save are UI-local (`state.list_dir`/
`read_file`/`save_file`); the daemon is involved only for asks (the normal
`chat` op carrying `cwd`/`open_file`) and ✎ Edit proposals
(`files.propose_edit`). A proposal renders as a colored diff with
Apply / Discard — the preview IS the confirmation (compose precedent), and
nothing touches disk until Apply."""
import difflib
import html
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QLineEdit, QPlainTextEdit, QWidget

from .. import theme as T
from ..state import AppState
from ..widgets import (
    ClickRow, ElideLabel, HtmlBody, TypingDots, button, clear_layout,
    empty_state, font, hbox, hline, label, scroll, vbox, vline,
)

DIFF_MAX_LINES = 600     # a runaway diff must not freeze the pane


class FilesScreen(QWidget):
    def __init__(self, state: AppState, chat_client=None):
        super().__init__()
        self.state = state
        self.chat = chat_client if chat_client is not None else state._chat
        self.cwd: str | None = None
        self.open_path: str | None = None
        self._dirty = False
        self._loading = False      # setPlainText must not read as a user edit
        self._proposal: str | None = None
        self._conv_id: int | None = None   # this screen's own thread
        self._busy = False
        self._acc = ""

        root = vbox(self)

        # ---- workbench: browser | editor ---------------------------------
        work = hbox(s=0)

        col = QWidget()
        col.setFixedWidth(300)
        cv = vbox(col)
        head = QWidget()
        hv = vbox(head, (14, 14, 14, 10), 8)
        path_row = hbox(s=6)
        self.path_box = QLineEdit()
        f = self.path_box.font()
        f.setPixelSize(11)
        self.path_box.setFont(f)
        self.path_box.returnPressed.connect(
            lambda: self.navigate(self.path_box.text()))
        path_row.addWidget(self.path_box, 1)
        home_btn = button("⌂", "outline", px=12)
        home_btn.setFixedSize(26, 26)
        home_btn.setStyleSheet("padding: 0px;")   # glyph fits a square button
        home_btn.setToolTip("Home folder")
        home_btn.clicked.connect(lambda: self.navigate(Path.home()))
        path_row.addWidget(home_btn)
        self.up_btn = button("↑", "outline", px=12)
        self.up_btn.setFixedSize(26, 26)
        self.up_btn.setStyleSheet("padding: 0px;")
        self.up_btn.setToolTip("Up one folder")
        self.up_btn.clicked.connect(self._go_up)
        path_row.addWidget(self.up_btn)
        hv.addLayout(path_row)
        self.dir_status = label("", 11, T.TEXT_DIM)
        hv.addWidget(self.dir_status)
        cv.addWidget(head)
        cv.addWidget(hline(T.BORDER_SOFT))
        rows_host = QWidget()
        self.rows_lay = vbox(rows_host, (0, 0, 0, 0), 0)
        self.rows_lay.addStretch(1)
        cv.addWidget(scroll(rows_host), 1)
        work.addWidget(col)
        work.addWidget(vline(T.BORDER_SOFT))

        pane = QWidget()
        pv = vbox(pane)
        phead = QWidget()
        ph = hbox(phead, (16, 12, 16, 10), 10)
        self.file_lab = ElideLabel("No file open", 13, T.TEXT_DIM, 500)
        ph.addWidget(self.file_lab, 1)
        self.meta_lab = label("", 11, T.TEXT_DIM)
        ph.addWidget(self.meta_lab)
        self.edit_btn = button("✎ Edit", "outline", px=11)
        self.edit_btn.setFixedHeight(26)
        self.edit_btn.setToolTip(
            "Ask Lumen to revise this file — type the change below first "
            "(runs the local model once; nothing is written until you Apply)")
        self.edit_btn.clicked.connect(self._request_edit)
        ph.addWidget(self.edit_btn)
        self.save_btn = button("Save", "primary", px=11)
        self.save_btn.setFixedHeight(26)
        self.save_btn.setToolTip("Write your edits back to the file (Ctrl+S)")
        self.save_btn.clicked.connect(self._save)
        ph.addWidget(self.save_btn)
        pv.addWidget(phead)
        pv.addWidget(hline(T.BORDER_SOFT))

        body = QWidget()
        self.body_lay = vbox(body, (16, 12, 16, 12), 0)
        self.editor = QPlainTextEdit()
        self.editor.setFont(font(12))
        self.editor.textChanged.connect(self._on_edited)
        self.editor.hide()
        self.body_lay.addWidget(self.editor, 1)
        self.placeholder_lay = vbox(s=0)
        self.placeholder_lay.addWidget(empty_state(
            "No file open", "click a file in the browser to open it"))
        self.body_lay.addLayout(self.placeholder_lay)

        # diff preview host (hidden until a proposal arrives)
        self.diff_host = QWidget()
        dv = vbox(self.diff_host, (0, 0, 0, 0), 8)
        bar = hbox(s=8)
        bar.addWidget(label("Proposed change — nothing is written until you "
                            "apply it", 11, T.WARN))
        bar.addStretch(1)
        self.apply_btn = button("Apply edit", "primary", px=11)
        self.apply_btn.setFixedHeight(26)
        self.apply_btn.clicked.connect(self._apply_proposal)
        bar.addWidget(self.apply_btn)
        discard_btn = button("Discard", "outline", px=11)
        discard_btn.setFixedHeight(26)
        discard_btn.clicked.connect(self._discard_proposal)
        bar.addWidget(discard_btn)
        dv.addLayout(bar)
        diff_body = QWidget()
        self.diff_lay = vbox(diff_body, (0, 0, 0, 0), 0)
        self.diff_lay.addStretch(1)
        dv.addWidget(scroll(diff_body), 1)
        self.diff_host.hide()
        self.body_lay.addWidget(self.diff_host, 1)
        pv.addWidget(body, 1)
        work.addWidget(pane, 1)
        root.addLayout(work, 1)

        # ---- answer panel + prompt row -----------------------------------
        self.answer_host = QWidget()
        av = vbox(self.answer_host, (20, 10, 20, 10), 4)
        self.tool_lab = label("", 10, T.TEXT_FAINT)
        self.tool_lab.hide()
        av.addWidget(self.tool_lab)
        av.addWidget(label("LUMEN", 10, T.ACCENT, ls=1))
        self.thinking = None
        self.answer_lab = label("", 13, T.TEXT_PRIMARY, sans=True, wrap=True)
        self.thinking_slot = vbox(s=0)
        av.addLayout(self.thinking_slot)
        av.addWidget(self.answer_lab)
        answer_scroll = scroll(self.answer_host)
        answer_scroll.setMaximumHeight(190)
        self.answer_scroll = answer_scroll
        answer_scroll.hide()
        root.addWidget(hline(T.BORDER_SOFT))
        root.addWidget(answer_scroll)

        inrow = hbox(m=(20, 10, 20, 12), s=10)
        inrow.addWidget(label("❯", 15, T.ACCENT, 700))
        self.input = QLineEdit()
        self.input.setProperty("cls", "bare")
        self.input.setPlaceholderText(
            "Ask about this folder or file — or describe a change and press ✎ Edit")
        f = self.input.font()
        f.setPixelSize(14)
        self.input.setFont(f)
        self.input.returnPressed.connect(self._ask)
        inrow.addWidget(self.input, 1)
        self.ask_btn = button("Ask", "primary", px=11)
        self.ask_btn.setFixedHeight(28)
        self.ask_btn.clicked.connect(self._ask)
        inrow.addWidget(self.ask_btn)
        root.addLayout(inrow)

        QShortcut(QKeySequence("Ctrl+S"), self, self._save,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)

        if self.chat is not None:
            self.chat.chunk.connect(self._on_chunk)
            self.chat.done.connect(self._on_done)
            self.chat.error.connect(self._on_error)
            self.chat.tool_used.connect(self._on_tool)
            self.chat.conversation.connect(self._on_conversation)

        self._update_header()
        self.navigate(Path.home())

    # ---- browsing --------------------------------------------------------
    def navigate(self, path):
        p = Path(str(path)).expanduser()
        listing = self.state.list_dir(p)
        self.cwd = listing["path"]
        self.path_box.setText(self.cwd)
        self._parent = listing["parent"]
        self.up_btn.setEnabled(self._parent is not None)
        if listing["error"]:
            self.dir_status.setText(listing["error"])
        else:
            n = len(listing["entries"]) + listing["truncated"]
            extra = (f" · showing first {len(listing['entries'])}"
                     if listing["truncated"] else "")
            self.dir_status.setText(f"{n} items{extra}")
        clear_layout(self.rows_lay)
        if listing["error"] or not listing["entries"]:
            self.rows_lay.addWidget(empty_state(
                "Can't list this folder" if listing["error"] else "Empty folder",
                listing["error"] or None))
            self.rows_lay.addStretch(1)
            return
        for e in listing["entries"]:
            self._add_row(e)
        self.rows_lay.addStretch(1)

    def _add_row(self, e: dict):
        full = str(Path(self.cwd) / e["name"])
        if e["is_dir"]:
            row = ClickRow(lambda p=full: self.navigate(p))
        else:
            row = ClickRow(lambda p=full: self.open_file(p))
        row.setProperty("cls", "selrow")
        row.setProperty("sel", "true" if full == self.open_path else "false")
        rl = hbox(row, (12, 7, 12, 7), 8)
        if e["is_dir"]:
            rl.addWidget(ElideLabel(e["name"] + "/", 12, T.TEXT_PRIMARY, 500), 1)
        else:
            rl.addWidget(ElideLabel(e["name"], 12, T.TEXT_SECONDARY), 1)
            from lumen.daemon.connectors.local_files import size_label
            rl.addWidget(label(size_label(e["size"]), 10, T.TEXT_DIM))
        self.rows_lay.addWidget(row)

    def _go_up(self):
        if getattr(self, "_parent", None):
            self.navigate(self._parent)

    # ---- editor ----------------------------------------------------------
    def open_file(self, path: str):
        self._discard_proposal()
        got = self.state.read_file(path)
        self.open_path = path
        self._dirty = False
        clear_layout(self.placeholder_lay)
        if "error" in got:
            self.editor.hide()
            self.open_path = None
            self.placeholder_lay.addWidget(empty_state(
                Path(path).name, f"can't open — {got['error']}"))
        else:
            self._loading = True
            self.editor.setPlainText(got["content"])
            self._loading = False
            self.editor.show()
        self._update_header(path if "error" in got else None)
        self.navigate(self.cwd)      # re-render rows so the selection moves

    def _on_edited(self):
        if self._loading or self.open_path is None:
            return
        if not self._dirty:
            self._dirty = True
            self._update_header()

    def _save(self):
        if self.open_path is None or not self._dirty:
            return
        got = self.state.save_file(self.open_path, self.editor.toPlainText())
        if got.get("ok"):
            self._dirty = False
            self._update_header()
            self.state.toast_requested.emit(f"✓ Saved {Path(self.open_path).name}")
        else:
            self.state.toast_requested.emit(f"Couldn't save: {got.get('error')}")

    def _update_header(self, broken_path: str | None = None):
        shown = broken_path or self.open_path
        if shown is None:
            self.file_lab.setText("No file open")
            self._tint_file_lab(T.TEXT_DIM)
            self.meta_lab.setText("")
        else:
            self.file_lab.setText(Path(shown).name)
            self._tint_file_lab(T.TEXT_PRIMARY)
            size = len(self.editor.toPlainText().encode()) if self.open_path else 0
            from lumen.daemon.connectors.local_files import size_label
            self.meta_lab.setText(size_label(size)
                                  + (" · ● edited" if self._dirty else ""))
        can_edit = self.open_path is not None and self._proposal is None
        self.edit_btn.setEnabled(can_edit)
        self.save_btn.setEnabled(self.open_path is not None and self._dirty)

    def _tint_file_lab(self, color: str):
        from PyQt6.QtGui import QPalette
        from ..widgets import qcolor
        pal = self.file_lab.palette()
        pal.setColor(QPalette.ColorRole.WindowText, qcolor(color))
        self.file_lab.setPalette(pal)

    # ---- assisted edits --------------------------------------------------
    def _request_edit(self):
        if self.open_path is None or self._proposal is not None:
            return
        instruction = self.input.text().strip()
        if not instruction:
            self.state.toast_requested.emit(
                "Describe the change in the box below, then press ✎ Edit")
            return
        self.edit_btn.setEnabled(False)
        self.edit_btn.setText("thinking…")
        self.state.propose_edit(self.open_path, self.editor.toPlainText(),
                                instruction, self._on_proposal)

    def _on_proposal(self, result: dict):
        self.edit_btn.setText("✎ Edit")
        result = result or {}
        if not result.get("ok"):
            self.state.toast_requested.emit(
                result.get("message") or "No revision produced.")
            self._update_header()
            return
        self._proposal = result["content"]
        self.input.clear()
        self._render_diff(self.editor.toPlainText(), self._proposal)
        self.editor.hide()
        self.diff_host.show()
        self._update_header()

    def _render_diff(self, old: str, new: str):
        colors = {"+": T.OK, "-": T.NOW, "@": T.INFO}
        lines = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile="current", tofile="proposed", lineterm=""))
        shown = lines[:DIFF_MAX_LINES]
        parts = []
        for ln in shown:
            color = colors.get(ln[:1], T.TEXT_DIM)
            parts.append(f'<span style="color:{color}">{html.escape(ln)}</span>')
        if len(lines) > DIFF_MAX_LINES:
            parts.append(f'<span style="color:{T.TEXT_DIM}">…'
                         f'{len(lines) - DIFF_MAX_LINES} more lines</span>')
        clear_layout(self.diff_lay)
        body = HtmlBody(
            f'<pre style="font-family: \'{T.FONT_MONO}\'; font-size: 12px; '
            f'margin: 0; white-space: pre-wrap;">' + "\n".join(parts) + "</pre>")
        # HtmlBody's default is mail's white paper card; a diff belongs on the
        # editor's dark field so the +/- colors read.
        body.setStyleSheet(
            f"QTextBrowser {{ background: {T.BG_FIELD}; color: {T.TEXT_PRIMARY}; "
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 8px; "
            "padding: 10px; }")
        self.diff_lay.insertWidget(0, body)
        self.diff_lay.addStretch(1)   # clear_layout dropped the old stretch

    def _apply_proposal(self):
        if self._proposal is None or self.open_path is None:
            return
        got = self.state.save_file(self.open_path, self._proposal)
        if not got.get("ok"):
            self.state.toast_requested.emit(f"Couldn't save: {got.get('error')}")
            return
        self._loading = True
        self.editor.setPlainText(self._proposal)
        self._loading = False
        self._dirty = False
        self.state.toast_requested.emit(
            f"✓ Applied to {Path(self.open_path).name}")
        self._discard_proposal()

    def _discard_proposal(self):
        self._proposal = None
        self.diff_host.hide()
        clear_layout(self.diff_lay)
        if self.open_path is not None:
            self.editor.show()
        self._update_header()

    # ---- asks ------------------------------------------------------------
    def _ask(self):
        msg = self.input.text().strip()
        if not msg or self._busy:
            return
        if self.chat is None:
            self._show_answer("Sample mode — asks need the daemon running.")
            return
        self._busy = True
        self._acc = ""
        self.input.clear()
        self.answer_lab.setText("")
        self.tool_lab.hide()
        self.answer_scroll.show()
        self._start_thinking()
        payload = {"message": msg, "cwd": self.cwd}
        if self.open_path is not None:
            payload["open_file"] = self.open_path
        if self._conv_id is not None:
            payload["conversation_id"] = self._conv_id
        self.chat.send("chat", payload)

    def _show_answer(self, text: str):
        self.answer_scroll.show()
        self._stop_thinking()
        self.answer_lab.setText(text)

    def _start_thinking(self):
        self._stop_thinking()
        self.thinking = TypingDots("Thinking", 13, T.INFO, sans=True)
        self.thinking_slot.addWidget(self.thinking)
        self.thinking.start()

    def _stop_thinking(self):
        if self.thinking is not None:
            self.thinking.stop()
            self.thinking.setParent(None)
            self.thinking.deleteLater()
            self.thinking = None

    def _on_conversation(self, cid: int):
        if self._busy:
            self._conv_id = cid    # this screen's thread, not the chat tab's

    def _on_tool(self, name: str):
        if self._busy:
            self.tool_lab.setText(f"🔧 used {name}")
            self.tool_lab.show()

    def _on_chunk(self, text: str):
        if not self._busy:
            return
        self._stop_thinking()
        self._acc += text
        self.answer_lab.setText(self._acc)

    def _on_done(self):
        if not self._busy:
            return
        self._busy = False
        self._stop_thinking()
        if not self._acc:
            self.answer_lab.setText("(no answer)")

    def _on_error(self, msg: str):
        if not self._busy:
            return
        self._busy = False
        self._stop_thinking()
        self.answer_lab.setText(msg)
