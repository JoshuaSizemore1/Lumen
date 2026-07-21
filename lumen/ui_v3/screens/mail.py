"""Mail — the mockup's 346px list + reading pane, carrying Lumen's real inbox.

The mock's tag-filter chips become Lumen's scopes (inbox / unread / sent) plus
its Gmail labels, and its "✦ auto-tag" becomes the real suggest-labels pass:
suggestions render as dashed chips that write nothing until tapped.
"""
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLineEdit, QWidget

# Body rendering is pure HTML normalization with no theme coupling, so ui_v3
# shares ui_v2's module rather than forking it.
from ...ui_v2 import mail_html
from .. import theme as T
from ..components import MailRow, accent_fill
from ..widgets import (
    Avatar, Chip, ClickChip, ClickLabel, FlowLayout, Glyph, HtmlBody,
    IconButton, button,
    clear_layout, empty_state, eyebrow, font, hbox, hline, label, scroll, vbox,
    vline,
)


class _ImageLoader(QThread):
    """Fetches an email's remote images off the GUI thread, handing back the
    HTML with them inlined as data: URIs."""

    loaded = pyqtSignal(str, str)   # (message_id, inlined_html)

    def __init__(self, mid: str, html: str):
        super().__init__()
        self._mid, self._html = mid, html

    def run(self):
        self.loaded.emit(self._mid, mail_html.inline_remote_images(self._html))


class MailScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self._loaded_images: dict[str, str] = {}
        self._img_workers: set[_ImageLoader] = set()
        self._auto_loading: set[str] = set()

        root = hbox(self, (0, 0, 0, 0), 0)

        # ---- list column: sticky header outside the scroll ----------------
        col = QWidget()
        col.setFixedWidth(T.MAIL_LIST_W)
        cv = vbox(col, (0, 0, 0, 0), 0)
        cv.addWidget(self._list_header())
        cv.addWidget(hline(T.BORDER_MED))
        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, (0, 0, 0, 0), 0)
        cv.addWidget(scroll(self.list_host), 1)
        root.addWidget(col)
        root.addWidget(vline(T.BORDER_MED))

        # ---- reading pane (greedy) ---------------------------------------
        self.pane_host = QWidget()
        self.pane_lay = vbox(self.pane_host, (32, 24, 32, 32), 0)
        root.addWidget(scroll(self.pane_host), 1)

        state.mails_changed.connect(self.rebuild)
        self.rebuild()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.state.sync_inbox()

    # ---- header -----------------------------------------------------------
    def _list_header(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)

        top = hbox(m=(16, 15, 16, 9), s=8)
        top.addWidget(label("Inbox", 20, T.TEXT_PRIMARY, 600))
        self.unread_lab = label("", 10, T.TEXT_FAINT, mono=True)
        top.addWidget(self.unread_lab, 0, Qt.AlignmentFlag.AlignBottom)
        top.addStretch(1)
        refresh = IconButton("refresh", size=27, tooltip="Sync with Gmail",
                             on_click=self.state.refresh_inbox)
        top.addWidget(refresh)
        compose = button("✎ Compose", "primary", px=12, height=27)
        compose.clicked.connect(lambda: self.state.open_compose({}))
        top.addWidget(compose)
        v.addLayout(top)

        search_panel = QFrame()
        search_panel.setProperty("role", "panel")
        srow = hbox(search_panel, (11, 6, 11, 6), 8)
        srow.addWidget(Glyph("search", 13))
        self.search = QLineEdit()
        self.search.setProperty("cls", "bare")
        self.search.setPlaceholderText("Search mail…")
        self.search.setFont(font(12.5))
        self.search.returnPressed.connect(
            lambda: self.state.search_mails(self.search.text()))
        srow.addWidget(self.search, 1)
        wrap = hbox(m=(16, 0, 16, 9), s=0)
        wrap.addWidget(search_panel)
        v.addLayout(wrap)

        self.chip_host = QWidget()
        self.chip_lay = FlowLayout(self.chip_host, hgap=5, vgap=5)
        cwrap = hbox(m=(16, 0, 16, 11), s=0)
        cwrap.addWidget(self.chip_host)
        v.addLayout(cwrap)

        self.review = QFrame()
        self.review.setProperty("role", "panel")
        self.review_lay = hbox(self.review, (11, 7, 11, 7), 8)
        self.review.hide()
        rwrap = hbox(m=(16, 0, 16, 10), s=0)
        rwrap.addWidget(self.review)
        v.addLayout(rwrap)
        return w

    # ---- build ------------------------------------------------------------
    def rebuild(self):
        n = self.state.unread_count()
        self.unread_lab.setText(f"{n} unread")
        self._build_chips()
        self._build_review()
        self._build_list()
        self._build_pane()

    def _build_chips(self):
        clear_layout(self.chip_lay)
        scopes = ["inbox", "unread", "sent"] + list(self.state.mail_labels)
        for name in scopes:
            on = self.state.mail_scope == name
            self.chip_lay.addWidget(ClickChip(
                name.upper(), T.ACCENT_ON if on else T.label_color(name),
                T.ACCENT if on else T.BORDER_STRONG,
                bg=T.ACCENT if on else None,
                on_click=lambda n=name: self.state.set_mail_scope(n)))
        self.chip_lay.addWidget(ClickChip(
            "✦ suggest labels", T.ACCENT, T.ACCENT, px=9, dashed=True,
            tooltip="Classify unlabeled inbox mail on-device — nothing is "
                    "written until you tap a suggestion",
            on_click=self._suggest))
        self.chip_lay.addWidget(ClickChip(
            "⚑ rules", T.TEXT_SECONDARY, T.BORDER_STRONG, px=9,
            tooltip="Mail rules", on_click=self.state.open_rule_editor))

    def _build_review(self):
        clear_layout(self.review_lay)
        sug = self.state.mail_suggestions
        if not sug:
            self.review.hide()
            return
        self.review.show()
        by_label: dict[str, int] = {}
        for name in sug.values():
            by_label[name] = by_label.get(name, 0) + 1
        self.review_lay.addWidget(label(f"{len(sug)} suggested", 10, T.ACCENT,
                                        mono=True))
        for name, n in sorted(by_label.items()):
            self.review_lay.addWidget(ClickChip(
                f"file {n} → {name}", T.ACCENT, T.ACCENT, bg=T.ACCENT_SOFT,
                px=9, on_click=lambda l=name: self.state.accept_all_for_label(l)))
        self.review_lay.addStretch(1)
        self.review_lay.addWidget(ClickLabel(
            "✕", 11, T.TEXT_GHOST, on_click=self.state.dismiss_suggestions,
            tooltip="Dismiss all suggestions"))

    def _build_list(self):
        clear_layout(self.list_lay)
        mails = self.state.mails
        if not mails:
            self.list_lay.addWidget(empty_state(
                "No mail matches",
                "Clear the search or pick another label"))
            return
        sel = self.state.selected_mail
        for m in mails:
            mid = m["id"]
            sug = self.state.mail_suggestions.get(mid)
            suggestion = None
            if sug:
                suggestion = (sug,
                              lambda i=mid: self.state.apply_suggestion(i),
                              lambda i=mid: self.state.reject_suggestion(i))
            self.list_lay.addWidget(MailRow(
                m, selected=(mid == sel),
                on_click=lambda i=mid: self.state.select_mail(i),
                on_delete=lambda i=mid: self.state.delete_mail(i),
                suggestion=suggestion))
        self.list_lay.addStretch(1)

    # ---- reading pane -----------------------------------------------------
    def _build_pane(self):
        clear_layout(self.pane_lay)
        m = self.state.sel_mail()
        if m is None:
            self.pane_lay.addWidget(empty_state("No message selected"))
            return

        subj = label(m.get("subj", ""), 27, T.TEXT_PRIMARY, 500, wrap=True,
                     ls=-0.3)
        self.pane_lay.addWidget(subj)
        self.pane_lay.addSpacing(14)

        row = hbox(m=(0, 0, 0, 16), s=12)
        initial = (m.get("from", "?").strip() or "?")[0].upper()
        row.addWidget(Avatar(initial))
        who = vbox(m=(0, 0, 0, 0), s=1)
        who.addWidget(label(m.get("from", ""), 14, T.TEXT_PRIMARY, 600))
        who.addWidget(label(f"to me · {m.get('date', '')}", 10.5, T.TEXT_MUTED,
                            mono=True))
        row.addLayout(who, 1)

        reply = button("↳ Reply", "primary", px=14, height=32)
        reply.clicked.connect(lambda: self._reply(m))
        row.addWidget(reply)
        arch = button("Archive", "ghost", px=14, height=32)
        arch.clicked.connect(lambda: self.state.archive_mail(m["id"]))
        row.addWidget(arch)
        dele = button("Delete", "danger", px=14, height=32)
        dele.setToolTip("Move to Gmail Trash")
        dele.clicked.connect(lambda: self.state.delete_mail(m["id"]))
        row.addWidget(dele)
        self.pane_lay.addLayout(row)
        self.pane_lay.addWidget(hline(T.BORDER_MED))
        self.pane_lay.addSpacing(18)

        labels = m.get("label_names") or []
        if labels:
            lrow = hbox(m=(0, 0, 0, 12), s=5)
            for n in labels:
                lrow.addWidget(Chip(n, T.label_color(n), T.BORDER_MED,
                                    px=9, ls=0.5))
            lrow.addStretch(1)
            self.pane_lay.addLayout(lrow)

        attachments = m.get("attachments") or []
        if attachments:
            self.pane_lay.addWidget(label("⧉ " + ", ".join(attachments), 11,
                                          T.TEXT_MUTED))
            self.pane_lay.addSpacing(10)

        self.pane_lay.addWidget(self._body(m))
        self.pane_lay.addStretch(1)

    def _body(self, m: dict) -> QWidget:
        mid = m["id"]
        raw_html = m.get("body_html")
        if raw_html:
            loaded = self._loaded_images.get(mid)
            if loaded is not None:
                return HtmlBody(mail_html.prepare_html(loaded))
            hidden = mail_html.remote_image_count(raw_html)
            if hidden and self.state.load_remote_images:
                self._load_images(mid, raw_html)
                return HtmlBody(mail_html.prepare_html(
                    mail_html.strip_remote_images(raw_html)))
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
        # Plain-text mail sits directly on the page, as the mock draws it. The
        # white "paper" card exists to host HTML mail that assumes a light
        # background; wrapping plain prose in it would be a needless box.
        body = label(m.get("body", ""), 15, T.TEXT_BODY, wrap=True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return body

    def _load_images(self, mid: str, html: str):
        if mid in self._auto_loading:
            return
        self._auto_loading.add(mid)
        worker = _ImageLoader(mid, html)
        self._img_workers.add(worker)
        worker.loaded.connect(self._images_ready)
        worker.finished.connect(lambda w=worker: self._img_workers.discard(w))
        worker.start()

    def _images_ready(self, mid: str, html: str):
        self._loaded_images[mid] = html
        self._auto_loading.discard(mid)
        if self.state.selected_mail == mid:
            self._build_pane()

    # ---- actions ----------------------------------------------------------
    def _reply(self, m: dict):
        subj = m.get("subj", "")
        if not subj.lower().startswith("re:"):
            subj = f"Re: {subj}"
        self.state.open_compose({"to": m.get("from_addr") or m.get("from", ""),
                                 "subject": subj, "body": ""})

    def _suggest(self):
        self.state.suggest_labels()
