"""Mail — the mockup's 346px list + reading pane, carrying Lumen's real inbox.

The mock's tag-filter chips become Lumen's scopes (inbox / unread / sent) plus
its Gmail labels, and its "✦ auto-tag" becomes the real suggest-labels pass:
suggestions render as dashed chips that write nothing until tapped.
"""
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QFrame, QLineEdit, QProgressBar, QWidget

# Body rendering is pure HTML normalization with no theme coupling, so ui_v3
# shares ui_v2's module rather than forking it.
from ...ui_v2 import mail_html
from .. import theme as T
# One image loader for both the inbox pane and the suggest-review popup (#32).
from ..mail_body import ImageLoader as _ImageLoader
from ..components import MailRow, accent_fill
from ..widgets import (
    Avatar, Chip, ClickChip, ClickLabel, ClickRow, Dot, FlowLayout, Glyph,
    HtmlBody, IconButton, button,
    clear_layout, empty_state, eyebrow, font, hbox, hline, label, scroll, vbox,
    vline,
)

# Width of the collapsible mailbox column (#10).
NAV_W = 190


class _BusyOverlay(QWidget):
    """Dims the mail screen and shows an indeterminate progress bar while a
    long inbox-wide task runs (suggest-labels, #7). A full-rect child so a
    click can't reach the list behind it until the work finishes."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("scrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = vbox(self, (0, 0, 0, 0), 12)
        v.addStretch(1)
        self._label = label("", 14, T.TEXT_PRIMARY, 600)
        v.addWidget(self._label, 0, Qt.AlignmentFlag.AlignHCenter)
        bar = QProgressBar()
        bar.setRange(0, 0)                 # indeterminate — the "loading circle"
        bar.setTextVisible(False)
        bar.setFixedWidth(T.sc(220))
        v.addWidget(bar, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addStretch(1)
        self.hide()

    def start(self, message: str):
        self._label.setText(message)
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()

    def mousePressEvent(self, ev):
        ev.accept()                        # swallow clicks while busy


class MailScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self._loaded_images: dict[str, str] = {}
        self._img_workers: set[_ImageLoader] = set()
        self._auto_loading: set[str] = set()
        # Preserve the inbox scroll position across rebuilds that don't change
        # which mails are listed (opening a message, its body/images finishing
        # loading) — those must not yank the list back to the top (#1). A real
        # membership change (scope switch, search, new mail) still resets it.
        self._last_mail_ids: list[str] = []

        self._nav_shown = True

        root = hbox(self, (0, 0, 0, 0), 0)

        # ---- collapsible mailbox nav (#10) --------------------------------
        root.addWidget(self._folder_nav())
        self.nav_sep = vline(T.BORDER_MED)
        root.addWidget(self.nav_sep)

        # ---- list column: sticky header outside the scroll ----------------
        col = QWidget()
        col.setFixedWidth(T.MAIL_LIST_W)
        cv = vbox(col, (0, 0, 0, 0), 0)
        cv.addWidget(self._list_header())
        cv.addWidget(hline(T.BORDER_MED))
        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, (0, 0, 0, 0), 0)
        self.list_scroll = scroll(self.list_host)
        cv.addWidget(self.list_scroll, 1)
        root.addWidget(col)
        root.addWidget(vline(T.BORDER_MED))

        # ---- reading pane (greedy) ---------------------------------------
        self.pane_host = QWidget()
        self.pane_lay = vbox(self.pane_host, (32, 24, 32, 32), 0)
        root.addWidget(scroll(self.pane_host), 1)

        self.busy = _BusyOverlay(self)

        state.mails_changed.connect(self.rebuild)
        self.rebuild()

    # ---- folder nav (collapsible mailbox column, #10) --------------------
    def _folder_nav(self) -> QWidget:
        """A left column of mailboxes — Inbox / Unread / Sent, then each Gmail
        label — that can be collapsed to give the messages more room (#10)."""
        self.nav = QWidget()
        self.nav.setFixedWidth(T.sc(NAV_W))
        nv = vbox(self.nav, (12, 14, 10, 14), 2)

        top = hbox(m=(4, 0, 0, 6), s=6)
        top.addWidget(label("Mailboxes", 11, T.TEXT_FAINT, mono=True, ls=1))
        top.addStretch(1)
        top.addWidget(IconButton("chevron-left", size=22, tooltip="Hide mailboxes",
                                 on_click=lambda: self._set_nav(False)))
        nv.addLayout(top)

        self.nav_rows = QWidget()
        self.nav_rows_lay = vbox(self.nav_rows, (0, 0, 0, 0), 2)
        nv.addWidget(scroll(self.nav_rows), 1)
        return self.nav

    def _build_nav(self):
        clear_layout(self.nav_rows_lay)
        v = self.nav_rows_lay
        unread = self.state.unread_count()
        # Inbox here means "standard, unlabeled mail" — labeling moves a message
        # out of the inbox, so the inbox is exactly Josh's "standard" bucket.
        for key, name, count in (("inbox", "Inbox", None),
                                  ("unread", "Unread", unread),
                                  ("sent", "Sent", None)):
            v.addWidget(self._folder_row(key, name, count))
        labels = list(self.state.mail_labels)
        if labels:
            v.addSpacing(8)
            v.addWidget(label("LABELS", 9.5, T.TEXT_FAINTER, mono=True, ls=1.5))
            v.addSpacing(2)
            for name in labels:
                v.addWidget(self._folder_row(name, name, None,
                                             color=T.label_color(name)))
        v.addStretch(1)

    def _folder_row(self, key: str, name: str, count, color=None) -> QWidget:
        on = self.state.mail_scope == key
        row = ClickRow(lambda k=key: self.state.set_mail_scope(k))
        lay = hbox(row, (9, 7, 9, 7), 9)
        if color is not None:
            lay.addWidget(Dot(8, color, radius=2))
        lay.addWidget(label(name, 13,
                            T.TEXT_PRIMARY if on else T.TEXT_SECONDARY,
                            600 if on else 400), 1)
        if count:
            lay.addWidget(label(str(count), 10, T.TEXT_FAINTER, mono=True))
        if on:
            row.setStyleSheet(
                f"ClickRow {{ background: {accent_fill()}; border-radius: 5px; }}")
        return row

    def _set_nav(self, shown: bool):
        self._nav_shown = shown
        self.nav.setVisible(shown)
        self.nav_sep.setVisible(shown)
        self.nav_toggle.setVisible(not shown)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.busy.isVisible():
            self.busy.setGeometry(self.rect())

    def showEvent(self, ev):
        super().showEvent(ev)
        self.state.sync_inbox()

    # ---- header -----------------------------------------------------------
    def _list_header(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)

        top = hbox(m=(16, 15, 16, 9), s=8)
        # Reopens the mailbox nav when it's been collapsed (#10).
        self.nav_toggle = IconButton("chevron-right", size=27,
                                     tooltip="Show mailboxes",
                                     on_click=lambda: self._set_nav(True))
        self.nav_toggle.hide()
        top.addWidget(self.nav_toggle)
        self.scope_title = label("Inbox", 20, T.TEXT_PRIMARY, 600)
        top.addWidget(self.scope_title)
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

        return w

    # ---- build ------------------------------------------------------------
    def rebuild(self):
        n = self.state.unread_count()
        self.unread_lab.setText(f"{n} unread")
        self._build_nav()
        scope = self.state.mail_scope
        self.scope_title.setText(
            {"inbox": "Inbox", "unread": "Unread", "sent": "Sent"}.get(
                scope, scope))
        ids = [m["id"] for m in self.state.mails]
        keep_scroll = ids == self._last_mail_ids
        yoff = self.list_scroll.verticalScrollBar().value() if keep_scroll else 0
        self._last_mail_ids = ids
        self._build_chips()
        self._build_list()
        self._build_pane()
        # Restore after the layout settles: the scrollbar's range isn't valid
        # until the freshly-added rows have been laid out.
        if keep_scroll and yoff:
            QTimer.singleShot(
                0, lambda: self.list_scroll.verticalScrollBar().setValue(yoff))

    def _build_chips(self):
        # Scope selection (inbox/unread/sent/labels) moved to the collapsible
        # mailbox nav (#10); the chip row now carries only the action chips.
        clear_layout(self.chip_lay)
        self.chip_lay.addWidget(ClickChip(
            "✦ suggest labels", T.ACCENT, T.ACCENT, px=9, dashed=True,
            tooltip="Classify unlabeled inbox mail on-device — nothing is "
                    "written until you tap a suggestion",
            on_click=self._suggest))
        self.chip_lay.addWidget(ClickChip(
            "⚑ rules", T.TEXT_SECONDARY, T.BORDER_STRONG, px=9,
            tooltip="Mail rules", on_click=self.state.open_rule_editor))

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
            self.list_lay.addWidget(MailRow(
                m, selected=(mid == sel),
                on_click=lambda i=mid: self.state.select_mail(i),
                on_delete=lambda i=mid: self.state.delete_mail(i)))
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

        # Label row: current labels (each removable via its ×) plus a manual
        # "＋ Label" picker (#9) — Lumen no longer only labels via suggestions.
        labels = m.get("label_names") or []
        lrow = hbox(m=(0, 0, 0, 12), s=5)
        for n in labels:
            lrow.addWidget(ClickChip(
                f"{n}  ✕", T.label_color(n), T.BORDER_MED, px=9, ls=0.5,
                tooltip=f"Remove the {n} label",
                on_click=lambda name=n: self.state.remove_label(m["id"], name)))
        add = ClickChip("＋ Label", T.TEXT_SECONDARY, T.BORDER_STRONG, px=9,
                        tooltip="Add a label to this email",
                        on_click=lambda: self._label_menu(m, add))
        lrow.addWidget(add)
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
        # body_html is None while the daemon fetch is still in flight (select
        # always kicks one). Show a quiet placeholder rather than the plain
        # snippet, which would flash and then reflow into the real HTML (#2).
        # A message with no HTML part comes back as '' and falls through below.
        if raw_html is None and self.state.live:
            return self._loading_body()
        if raw_html:
            loaded = self._loaded_images.get(mid)
            if loaded is not None:
                return HtmlBody(mail_html.prepare_html(loaded))
            hidden = mail_html.remote_image_count(raw_html)
            if hidden and self.state.load_remote_images:
                # Don't paint the stripped body first: showing it and then
                # swapping in the image-laden version is exactly the partial
                # render Josh flagged (#2). Hold a placeholder until the images
                # finish, then render the message once, complete.
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
        # Plain-text mail sits directly on the page, as the mock draws it. The
        # white "paper" card exists to host HTML mail that assumes a light
        # background; wrapping plain prose in it would be a needless box.
        body = label(m.get("body", ""), 15, T.TEXT_BODY, wrap=True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return body

    def _loading_body(self) -> QWidget:
        """Placeholder shown while a message's HTML (or its remote images) is
        still loading, so the pane never flashes a partial render (#2)."""
        w = QWidget()
        v = vbox(w, (0, 40, 0, 0), 0)
        v.addWidget(label("Loading message…", 13, T.TEXT_FAINT),
                    0, Qt.AlignmentFlag.AlignHCenter)
        return w

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

    def _label_menu(self, m: dict, anchor: QWidget):
        """Manual label picker (#9): existing labels not already on the
        message, plus a 'New label…' entry that creates one on the fly."""
        from PyQt6.QtWidgets import QMenu
        applied = set(m.get("label_names") or [])
        menu = QMenu(self)
        for name in self.state.mail_labels:
            if name not in applied:
                menu.addAction(name,
                               lambda n=name: self.state.apply_label(m["id"], n))
        if not menu.isEmpty():
            menu.addSeparator()
        menu.addAction("New label…", lambda: self._new_label(m))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _new_label(self, m: dict):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New label", "Label name:")
        if ok and name.strip():
            self.state.apply_label(m["id"], name.strip())

    def _suggest(self):
        # Classifying is one model call per unlabeled message, so it can run for
        # a while — dim the inbox and show a busy bar until it returns (#7).
        self.busy.start("Classifying inbox mail…")
        self.state.suggest_labels(cb=self._suggest_done)

    def _suggest_done(self, _result):
        # Results now open a two-pane review popup rather than inline chips
        # (#32): the shell owns that window-level overlay, so ask it to open when
        # there's at least one suggestion.
        self.busy.hide()
        if self.state.mail_suggestions and hasattr(
                self.state, "suggest_review_requested"):
            self.state.suggest_review_requested.emit()
