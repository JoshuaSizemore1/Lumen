"""Reusable email-body renderer.

The reading experience — HTML normalization, lazy remote-image inlining off the
GUI thread, and a plain-text fallback — is the same wherever a message is read.
This widget owns that logic so the main inbox pane and the suggest-review popup
(#32) render bodies identically instead of forking the fiddly image handling.
"""
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QFrame, QWidget

from ..ui_v2 import mail_html
from . import theme as T
from .widgets import HtmlBody, button, clear_layout, hbox, label, vbox


class ImageLoader(QThread):
    """Fetches an email's remote images off the GUI thread, handing back the
    HTML with them inlined as data: URIs."""

    loaded = pyqtSignal(str, str)   # (message_id, inlined_html)

    def __init__(self, mid: str, html: str):
        super().__init__()
        self._mid, self._html = mid, html

    def run(self):
        self.loaded.emit(self._mid, mail_html.inline_remote_images(self._html))


class MailBodyView(QWidget):
    """Renders one message's body. Call `show_mail(row)` with a full mirror row
    (body_html present, or None while a fetch is in flight); the widget swaps in
    the complete render once images finish, never a partial one (#2)."""

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._loaded_images: dict[str, str] = {}
        self._img_workers: set[ImageLoader] = set()
        self._auto_loading: set[str] = set()
        self._mid: str | None = None
        self._m: dict | None = None
        self._lay = vbox(self, (0, 0, 0, 0), 0)

    def clear(self):
        self._mid = self._m = None
        clear_layout(self._lay)

    def show_mail(self, m: dict):
        self._mid, self._m = m["id"], m
        self._render()

    def _render(self):
        clear_layout(self._lay)
        if self._m is not None:
            self._lay.addWidget(self._body(self._m))

    # Mirrors MailScreen._body — kept in sync deliberately; see module docstring.
    def _body(self, m: dict) -> QWidget:
        mid = m["id"]
        raw_html = m.get("body_html")
        if raw_html is None and self.state.live:
            return self._loading_body()
        if raw_html:
            loaded = self._loaded_images.get(mid)
            if loaded is not None:
                return HtmlBody(mail_html.prepare_html(loaded))
            hidden = mail_html.remote_image_count(raw_html)
            if hidden and self.state.load_remote_images:
                self._load_images(mid, raw_html)
                return self._loading_body()
            if hidden:
                w = QWidget()
                v = vbox(w, (0, 0, 0, 0), 8)
                bar = QFrame()
                bar.setProperty("role", "panel")
                brow = hbox(bar, (12, 8, 12, 8), 10)
                brow.addWidget(label(
                    f"{hidden} remote image{'s' if hidden > 1 else ''} blocked",
                    12, T.TEXT_SECONDARY), 1)
                load = button("Load images", "soft", px=12, height=26)
                load.clicked.connect(lambda: self._load_images(mid, raw_html))
                brow.addWidget(load)
                v.addWidget(bar)
                v.addWidget(HtmlBody(mail_html.prepare_html(
                    mail_html.strip_remote_images(raw_html))))
                return w
            return HtmlBody(mail_html.prepare_html(raw_html))
        body = label(m.get("body", ""), 15, T.TEXT_BODY, wrap=True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return body

    def _loading_body(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 40, 0, 0), 0)
        v.addWidget(label("Loading message…", 13, T.TEXT_FAINT),
                    0, Qt.AlignmentFlag.AlignHCenter)
        return w

    def _load_images(self, mid: str, html: str):
        if mid in self._auto_loading:
            return
        self._auto_loading.add(mid)
        worker = ImageLoader(mid, html)
        self._img_workers.add(worker)
        worker.loaded.connect(self._images_ready)
        worker.finished.connect(lambda w=worker: self._img_workers.discard(w))
        worker.start()

    def _images_ready(self, mid: str, html: str):
        self._loaded_images[mid] = html
        self._auto_loading.discard(mid)
        if self._mid == mid:
            self._render()
