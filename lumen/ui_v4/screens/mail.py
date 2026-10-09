"""Inbox: mailboxes, the message list and a reading pane.

Keeps everything the ui_v3 Mail screen did:
- mailboxes: Inbox, Unread (with count), Sent, then every Gmail label; the
  column can be hidden and shown again (#10)
- search (Enter runs it against the mirror; clearing it reloads the scope)
- refresh = a real Gmail delta sync; showing the screen runs the debounced
  sync_inbox
- list rows with unread dot, sender, time, subject, preview, label chips and
  a delete button; clicking opens and marks read, Up/Down browse without
  marking read (#50)
- rows update in place and the list keeps your place across rebuilds (#1,
  #49)
- reading pane: subject, sender, date, Reply / Archive / Delete, label chips
  you can remove, "Add label" with existing labels or a new one (#9),
  attachments, and the HTML body with remote images loaded off the GUI
  thread or click-to-load (MailBody)
- Compose (state.open_compose), Rules (state.open_rule_editor)
- Suggest labels: dims the screen while the model works (#7), shows the
  model-off notice when the model is off, then opens the review overlay
  (#32) through state.suggest_review_requested
- archive, delete, send and rule changes all go through the daemon, which
  asks you to confirm before Gmail changes

New in v4:
- Forward and Mark as unread in the reading pane; replies thread (reply_to)
- a banner when label suggestions are waiting for review
- loading skeletons and "not connected" / empty-mailbox states
- Delete key trashes the open message (still confirmed by the daemon)
- below 900px the mailbox column hides and the list and message take turns
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QTextDocument
from PyQt6.QtWidgets import (
    QFrame, QLabel, QMenu, QProgressBar, QSizePolicy, QWidget,
)

from .. import theme as T
from ..components import (
    Avatar, Badge, Button, Card, ClickRow, Divider, Dot, ElideLabel, EmptyState,
    Eyebrow, FlowLayout, Heading, IconButton, IconLabel, Label, MailBody,
    ModelOffNotice, Panel, ScreenHeader, ScrollArea, SearchField, SkeletonRow,
    TagChip, TextField, clear_layout, fire_on_next_tick, hbox, vbox,
)
from ..overlays import _Overlay, _dialog_foot, _dialog_head

NAV_W = 208
LIST_W = 368
NARROW = 900            # content width below which list and message take turns
SCOPES = (("inbox", "Inbox", "inbox"), ("unread", "Unread", "mail"),
          ("sent", "Sent", "send"))


def _set_weight(lbl: QLabel, bold: bool) -> None:
    f = lbl.font()
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    lbl.setFont(f)


def _scope_name(scope: str) -> str:
    return {k: n for k, n, _i in SCOPES}.get(scope, scope)


def _plain_body(m: dict) -> str:
    body = (m.get("body") or "").strip()
    if not body and m.get("body_html"):
        doc = QTextDocument()
        doc.setHtml(m["body_html"])
        body = doc.toPlainText().strip()
    return body or m.get("preview", "")


# ---- pieces -------------------------------------------------------------------
class MailRow(ClickRow):
    """One message in the list. Unread/selected/text update in place, so a
    click never rebuilds fifty rows (ui_v3 perf work)."""

    def __init__(self, m: dict, selected: bool, on_open, on_delete):
        super().__init__(on_open, selected)
        self.mail_id = m["id"]
        self.setMinimumHeight(76)
        h = hbox(self, (T.S3, T.S3, T.S2, T.S3), T.S3)
        dot_col = vbox(m=(0, 7, 0, 0), s=0)
        self.dot = Dot("accent", 8)
        sp = self.dot.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.dot.setSizePolicy(sp)
        dot_col.addWidget(self.dot)
        dot_col.addStretch(1)
        h.addLayout(dot_col)

        col = vbox(s=2)
        top = hbox(s=T.S2)
        self.from_lab = ElideLabel(m.get("from", ""), "body")
        top.addWidget(self.from_lab, 1)
        self.time_lab = Label(m.get("time", ""), "meta")
        top.addWidget(self.time_lab, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(IconButton("trash", "Move to Gmail Trash", size=28,
                                 icon_size=T.ICON_SM, color="muted",
                                 on_click=on_delete))
        col.addLayout(top)
        self.subj_lab = ElideLabel(m.get("subj", ""), "small")
        col.addWidget(self.subj_lab)
        self.prev_lab = ElideLabel(m.get("preview", ""), "muted")
        col.addWidget(self.prev_lab)
        names = (m.get("label_names") or [])[:3]
        self._labels = tuple(names)
        if names:
            chips = hbox(m=(0, T.S1, 0, 0), s=T.S1)
            for n in names:
                chips.addWidget(TagChip(n))
            chips.addStretch(1)
            col.addLayout(chips)
        h.addLayout(col, 1)
        self._unread = None
        self.update_from(m, selected)

    def can_update_to(self, m: dict) -> bool:
        return (m.get("id") == self.mail_id
                and tuple((m.get("label_names") or [])[:3]) == self._labels)

    def update_from(self, m: dict, selected: bool) -> None:
        unread = bool(m.get("unread"))
        if unread != self._unread:
            self._unread = unread
            self.dot.setVisible(unread)
            _set_weight(self.from_lab, unread)
        self.set_selected(selected)
        for lab, key in ((self.from_lab, "from"), (self.subj_lab, "subj"),
                         (self.prev_lab, "preview"), (self.time_lab, "time")):
            if lab.text() != m.get(key, ""):
                lab.setText(m.get(key, ""))
        state = "Unread. " if unread else ""
        self.setAccessibleName(f"{state}{m.get('from', '')}: {m.get('subj', '')}")


class _LabelChip(QFrame):
    """A label on the open message, with its own remove button."""

    def __init__(self, name: str, on_remove):
        super().__init__()
        self.setProperty("role", "tag")
        h = hbox(self, (8, 2, 2, 2), 6)
        h.addWidget(Dot(lambda: T.tag_color(name), 8), 0,
                    Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(QLabel(name))
        h.addWidget(IconButton("x", f"Remove the {name} label", size=24,
                               icon_size=12, on_click=on_remove))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class _Busy(QWidget):
    """Dims the screen while suggest-labels runs (#7): one model call per
    unlabeled message, so it can take a while. Also carries the model-off
    notice, where a click on the dim closes it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("scrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = vbox(self, (T.S8, T.S8, T.S8, T.S8), 0)
        v.addStretch(1)
        self.card = Card(padding=T.S6, spacing=T.S3)
        self.card.setFixedWidth(400)
        self.title = Heading("", 3)
        self.card.lay.addWidget(self.title)
        self.note = Label("", "muted", wrap=True)
        self.card.lay.addWidget(self.note)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setAccessibleName("Working")
        self.card.lay.addWidget(self.bar)
        self.slot = vbox(s=0)
        self.card.lay.addLayout(self.slot)
        row = hbox(s=0)
        row.addStretch(1)
        row.addWidget(self.card)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        self.hide()

    def start(self, title: str, note: str):
        self.title.setText(title)
        self.note.setText(note)
        self.note.show()
        self.bar.show()
        clear_layout(self.slot)
        self._show()

    def notice(self, widget: QWidget, title: str):
        self.title.setText(title)
        self.note.hide()
        self.bar.hide()
        clear_layout(self.slot)
        self.slot.addWidget(widget)
        close = Button("Close", "secondary", size="sm", on_click=self.hide)
        self.slot.addSpacing(T.S2)
        self.slot.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self._show()

    def _show(self):
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()

    def mousePressEvent(self, ev):
        # While work runs there is nothing to dismiss; the click is swallowed.
        if self.bar.isHidden() and not self.card.geometry().contains(
                ev.position().toPoint()):
            self.hide()
        ev.accept()


class _NewLabelOverlay(_Overlay):
    """"New label…" from the label picker (#9)."""

    CARD_W = 440

    def __init__(self, window, state):
        super().__init__(window)
        self.state = state
        self._mid = None
        v = vbox(self.card, (0, 0, 0, 0), 0)
        head, _h = _dialog_head("New label", "Added in Gmail to this email.",
                                close=self.close_overlay)
        v.addLayout(head)
        body = vbox(m=(T.S6, 0, T.S6, T.S5), s=T.S4)
        self.name_f = TextField("Label name", "Labelled mail leaves the inbox.",
                                "Receipts")
        self.name_f.input.returnPressed.connect(self._add)
        body.addWidget(self.name_f)
        v.addLayout(body, 1)
        foot, fl = _dialog_foot()
        fl.addStretch(1)
        fl.addWidget(Button("Cancel", "secondary", on_click=self.close_overlay))
        fl.addWidget(Button("Add label", "primary", icon="tag", on_click=self._add))
        v.addWidget(foot)

    def open(self, mid: str):
        self._mid = mid
        self.name_f.clear_error()
        self.name_f.set_text("")
        self.pop()
        self.name_f.input.setFocus()

    def _add(self):
        if not self.isVisible():
            return
        name = self.name_f.text().strip()
        if not name:
            self.name_f.set_error("Type a name for the label.")
            return
        self.close_overlay()
        self.state.apply_label(self._mid, name)


# ---- screen -------------------------------------------------------------------
class MailScreen(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self._seen = (not self.state.live) or bool(self.state.mails)
        self._dirty = False
        self._pending = False
        self._narrow = False
        self._reading = False           # narrow mode: message instead of list
        self._nav_shown = True
        self._nav_sig = None
        self._pane_sig = None
        self._body_sig = None
        self._rows: dict[str, MailRow] = {}
        self._last_ids: list[str] = []
        self._query = ""
        self._label_dialog: _NewLabelOverlay | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        root = vbox(self, (0, 0, 0, 0), 0)

        # ---- header ---------------------------------------------------------
        head = QWidget()
        hv = vbox(head, (T.S8, T.S6, T.S8, T.S4), 0)
        self.header = ScreenHeader("Inbox", "", "Mail")
        self.refresh_btn = IconButton("refresh", "Check Gmail for new mail",
                                      on_click=self._sync_now)
        self.header.add_action(self.refresh_btn)
        self.suggest_btn = Button("Suggest labels", "secondary", icon="tag",
                                  on_click=self._suggest)
        self.suggest_btn.setToolTip("Lumen sorts unlabeled inbox mail on this "
                                    "computer. Nothing changes until you "
                                    "accept a suggestion.")
        self.header.add_action(self.suggest_btn)
        self.rules_btn = Button("Rules", "ghost", icon="filter",
                                on_click=lambda: self.state.open_rule_editor({}))
        self.rules_btn.setToolTip("Mail rules: label matching mail on every sync")
        self.header.add_action(self.rules_btn)
        self.header.add_action(Button("Compose", "primary", icon="edit",
                                      on_click=lambda: self.state.open_compose({})))
        hv.addWidget(self.header)
        root.addWidget(head)
        root.addWidget(Divider())

        body = hbox(s=0)
        root.addLayout(body, 1)

        # ---- mailboxes ------------------------------------------------------
        self.nav = QWidget()
        self.nav.setFixedWidth(NAV_W)
        nv = vbox(self.nav, (T.S3, T.S4, T.S3, T.S3), T.S1)
        top = hbox(m=(T.S2, 0, 0, T.S1), s=T.S2)
        top.addWidget(Eyebrow("Mailboxes"), 1, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(IconButton("chevron-left", "Hide mailboxes", size=32,
                                 icon_size=T.ICON_SM,
                                 on_click=lambda: self._set_nav(False)))
        nv.addLayout(top)
        nav_scroll = ScrollArea(s=2)
        self.nav_lay = nav_scroll.lay
        nv.addWidget(nav_scroll, 1)
        body.addWidget(self.nav)
        self.nav_sep = Divider(vertical=True)
        body.addWidget(self.nav_sep)

        # ---- list -----------------------------------------------------------
        self.list_col = QWidget()
        self.list_col.setFixedWidth(LIST_W)
        lv = vbox(self.list_col, (0, 0, 0, 0), 0)
        bar = hbox(m=(T.S3, T.S4, T.S4, T.S3), s=T.S2)
        self.nav_toggle = IconButton("chevron-right", "Show mailboxes", size=36,
                                     on_click=lambda: self._set_nav(True))
        self.nav_toggle.hide()
        bar.addWidget(self.nav_toggle)
        # Enter runs the search (it's a daemon query, not a local filter).
        self.search = SearchField("Search mail… (press Enter)")
        self.search.returnPressed.connect(self._run_search)
        self.search.textChanged.connect(self._search_edited)
        bar.addWidget(self.search, 1)
        lv.addLayout(bar)
        self.review_host = QWidget()
        self.review_lay = vbox(self.review_host, (T.S3, 0, T.S3, T.S3), 0)
        lv.addWidget(self.review_host)
        self.list_scroll = ScrollArea(m=(T.S2, 0, T.S2, T.S4), s=0)
        self.list_lay = self.list_scroll.lay
        lv.addWidget(self.list_scroll, 1)
        body.addWidget(self.list_col)
        self.list_sep = Divider(vertical=True)
        body.addWidget(self.list_sep)

        # ---- reading pane ---------------------------------------------------
        self.pane = ScrollArea(m=(T.S8, T.S6, T.S8, T.S8), s=0)
        self.pane_head_host = QWidget()
        self.pane_head = vbox(self.pane_head_host, (0, 0, 0, 0), 0)
        self.pane.lay.addWidget(self.pane_head_host)
        self.mail_body = MailBody(self.state)
        self.pane.lay.addWidget(self.mail_body)
        self.pane.lay.addStretch(1)
        body.addWidget(self.pane, 1)

        self.busy = _Busy(self)
        self.state.mails_changed.connect(self._on_mails)
        self.rebuild()

    # ---- hooks --------------------------------------------------------------
    def on_shown(self, mid=None, compose=False):
        if self._dirty:
            self.rebuild()
        self.state.sync_inbox()
        if mid:
            self.state.select_mail(mid)
            self._reading = True
            self._apply_layout()
        elif self._narrow:
            self._reading = False
            self._apply_layout()
        if compose:
            self.state.open_compose({})

    def refresh(self):
        self.state.refresh_inbox()

    def ask_context(self) -> str:
        st = self.state
        m = st.sel_mail()
        scope = _scope_name(st.mail_scope)
        if m is not None and (self._reading or not self._narrow):
            body = _plain_body(m)
            if len(body) > 6000:
                body = body[:6000] + "…"
            labels = ", ".join(m.get("label_names") or []) or "none"
            return (f"Inbox screen ({scope}), reading an email.\n"
                    f"From: {m.get('from', '')} <{m.get('from_addr', '')}>\n"
                    f"Date: {m.get('date', '')}\nSubject: {m.get('subj', '')}\n"
                    f"Labels: {labels}\n\n{body}")
        lines = [f"Inbox screen, {scope}: {st.unread_count()} unread."]
        lines += [f"- {x.get('from', '')}: {x.get('subj', '')}"
                  + (" (unread)" if x.get("unread") else "")
                  for x in st.mails[:15]]
        return "\n".join(lines)

    def nav_token(self):
        return (self.state.mail_scope, self.state.selected_mail)

    def nav_restore(self, token):
        scope, mid = token
        self.state.set_mail_scope(scope)
        if mid and any(m["id"] == mid for m in self.state.mails):
            self.state.select_mail_quiet(mid)

    def apply_theme(self):
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.busy.isVisible():
            self.busy.setGeometry(self.rect())
        narrow = self.width() < NARROW
        if narrow != self._narrow:
            self._narrow = narrow
            if narrow:
                self._nav_shown = False
            self._apply_layout()

    # ---- layout -------------------------------------------------------------
    def _set_nav(self, shown: bool):
        self._nav_shown = shown
        if shown and self._narrow:
            self._reading = False
        self._apply_layout()

    def _apply_layout(self):
        nav = self._nav_shown
        self.nav.setVisible(nav)
        self.nav_sep.setVisible(nav)
        self.nav_toggle.setVisible(not nav)
        if self._narrow:
            reading = self._reading and self.state.sel_mail() is not None
            self.list_col.setMaximumWidth(16777215)
            self.list_col.setMinimumWidth(0)
            self.list_col.setVisible(not reading)
            self.list_sep.setVisible(False)
            self.pane.setVisible(reading)
            if reading:
                self.nav.setVisible(False)
                self.nav_sep.setVisible(False)
        else:
            self.list_col.setFixedWidth(LIST_W)
            self.list_col.show()
            self.list_sep.show()
            self.pane.show()
        self._pane_sig = None           # the back button comes and goes
        self._build_pane()

    def _back_to_list(self):
        self._reading = False
        self._apply_layout()
        row = self._rows.get(self.state.selected_mail)
        if row is not None:
            row.setFocus()

    # ---- state --------------------------------------------------------------
    def _on_mails(self):
        self._seen = True
        if not self.isVisible():
            self._dirty = True
            return
        if self._pending:
            return
        self._pending = True

        def run():
            self._pending = False
            self.rebuild()
        QTimer.singleShot(0, run)

    def _sync_now(self):
        self.state.refresh_inbox()
        self.win.show_toast("Checking Gmail for new mail…")

    def _set_scope(self, scope: str):
        if scope == self.state.mail_scope and not self._query:
            return
        if self._query:
            self.search.blockSignals(True)
            self.search.setText("")
            self.search.blockSignals(False)
            self._query = ""
            if scope == self.state.mail_scope:
                self.state.refresh_mails()
                return
        self.state.set_mail_scope(scope)
        if self._narrow:
            self._nav_shown = False
            self._reading = False
            self._apply_layout()
        self.state.nav_location_changed.emit()

    def _run_search(self):
        self._query = self.search.text().strip()
        self.state.search_mails(self._query)

    def _search_edited(self, text: str):
        if not text.strip() and self._query:
            self._query = ""
            self.state.search_mails("")

    def _open(self, mid: str):
        self.state.select_mail(mid)
        if self._narrow:
            self._reading = True
            self._apply_layout()

    # ---- build --------------------------------------------------------------
    def rebuild(self):
        self._dirty = False
        st = self.state
        n = st.unread_count()
        self.header.set_title(_scope_name(st.mail_scope))
        bits = [f"{n} unread" if n else "Nothing unread"]
        if not st.mail_connected:
            bits.append("Gmail is offline")
        elif st.mail_syncing:
            bits.append("syncing")
        self.header.set_subtitle(", ".join(bits) + ".")
        self._build_nav()
        self._build_review()

        ids = [m["id"] for m in st.mails]
        same = bool(set(ids) & set(self._last_ids))
        anchor = self._scroll_anchor() if same else None
        self._last_ids = ids
        self._build_list()
        self._build_pane()
        if same:
            QTimer.singleShot(0, lambda: self._restore_anchor(anchor))
        else:
            QTimer.singleShot(0, lambda: self.list_scroll.verticalScrollBar()
                              .setValue(0))

    def _build_nav(self):
        st = self.state
        unread = st.unread_count()
        sig = (unread, tuple(st.mail_labels), st.mail_scope)
        if sig == self._nav_sig:
            return
        self._nav_sig = sig
        clear_layout(self.nav_lay)
        for key, name, icon in SCOPES:
            self.nav_lay.addWidget(self._nav_row(
                key, name, unread if key == "unread" else 0, icon=icon))
        if st.mail_labels:
            self.nav_lay.addSpacing(T.S3)
            e = Eyebrow("Labels")
            e.setContentsMargins(T.S2, 0, 0, T.S1)
            self.nav_lay.addWidget(e)
            for name in st.mail_labels:
                self.nav_lay.addWidget(self._nav_row(name, name, 0))
        self.nav_lay.addStretch(1)

    def _nav_row(self, key: str, name: str, count: int, icon: str | None = None):
        on = self.state.mail_scope == key
        row = ClickRow(lambda k=key: self._set_scope(k), selected=on,
                       accessible_name=f"{name}" + (f", {count} unread" if count
                                                    else ""))
        row.setMinimumHeight(36)
        h = hbox(row, (T.S2, 4, T.S2, 4), T.S3)
        if icon:
            h.addWidget(IconLabel(icon, "accent_soft_fg" if on else "fg2",
                                  T.ICON_SM))
        else:
            h.addWidget(Dot(lambda n=name: T.tag_color(n), 8), 0,
                        Qt.AlignmentFlag.AlignVCenter)
        lab = ElideLabel(name, "small")
        if on:
            _set_weight(lab, True)
        h.addWidget(lab, 1)
        if count:
            h.addWidget(Label(str(count), "meta"))
        return row

    def _build_review(self):
        clear_layout(self.review_lay)
        n = len(self.state.mail_suggestions)
        if not n:
            self.review_host.hide()
            return
        p = Panel(padding=T.S3, spacing=0)
        row = hbox(s=T.S2)
        row.addWidget(Badge("accent", f"{n} to review"), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(Label("Label suggestions are waiting.", "small", wrap=True), 1)
        row.addWidget(Button("Review", "secondary", size="sm",
                             on_click=self._open_review))
        p.lay.addLayout(row)
        self.review_lay.addWidget(p)
        self.review_host.show()

    def _open_review(self):
        sig = getattr(self.state, "suggest_review_requested", None)
        if sig is not None:
            sig.emit()
        else:
            self.win.suggest_review.open()

    def _build_list(self):
        st = self.state
        mails = st.mails
        sel = st.selected_mail
        rows = self._rows
        if (mails and len(rows) == len(mails)
                and all(m["id"] in rows and rows[m["id"]].can_update_to(m)
                        for m in mails)):
            for m in mails:
                rows[m["id"]].update_from(m, m["id"] == sel)
            return

        clear_layout(self.list_lay)
        self._rows = {}
        if not self._seen:
            for _ in range(7):
                self.list_lay.addWidget(SkeletonRow(3, height=76))
            self.list_lay.addStretch(1)
            return
        if not mails:
            self.list_lay.addWidget(self._empty_list())
            return
        for m in mails:
            mid = m["id"]
            row = MailRow(m, mid == sel,
                          on_open=lambda i=mid: self._open(i),
                          on_delete=lambda i=mid: self._delete(i))
            self._rows[mid] = row
            self.list_lay.addWidget(row)
        self.list_lay.addStretch(1)

    def _empty_list(self) -> QWidget:
        st = self.state
        if not st.mail_connected:
            return EmptyState(
                "mail", "Gmail isn't connected",
                "Lumen can't reach your Gmail account, so there's no mail to "
                "show. Reconnect it in Settings.", "Open Settings",
                lambda: self.win.switch_to("settings"))
        if self._query:
            return EmptyState(
                "search", "No mail matches",
                f"Nothing in your mail mentions “{self._query}”.",
                "Clear search", lambda: self.search.setText(""))
        scope = st.mail_scope
        if scope == "inbox":
            return EmptyState(
                "inbox", "Your inbox is empty",
                "Nothing is waiting. Mail you label moves out of the inbox.",
                "Check for new mail", self._sync_now)
        if scope == "unread":
            return EmptyState("check", "No unread mail",
                              "You've opened everything that has arrived.",
                              "Show inbox", lambda: self._set_scope("inbox"))
        return EmptyState("inbox", f"Nothing in {_scope_name(scope)}",
                          "There are no messages here yet.",
                          "Show inbox", lambda: self._set_scope("inbox"))

    # ---- keeping your place (#49) -------------------------------------------
    def _scroll_anchor(self):
        if not self._rows:
            return None
        y = self.list_scroll.verticalScrollBar().value()
        for m in self.state.mails:
            row = self._rows.get(m["id"])
            if row is not None and row.y() + row.height() > y:
                return m["id"], row.y() - y
        return None

    def _restore_anchor(self, anchor, settle: bool = True):
        """Twice on purpose: a fresh row's final position isn't known until
        the layout has run, and one deferred pass lands a row off."""
        if anchor is None:
            return
        mid, offset = anchor
        row = self._rows.get(mid)
        if row is None:
            return
        self.list_scroll.body.layout().activate()
        self.list_scroll.verticalScrollBar().setValue(max(0, row.y() - offset))
        if settle:
            QTimer.singleShot(0, lambda: self._restore_anchor(anchor, False))

    # ---- keyboard (#50) -----------------------------------------------------
    def keyPressEvent(self, ev):
        key = ev.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            if self._step(1 if key == Qt.Key.Key_Down else -1):
                ev.accept()
                return
        if key == Qt.Key.Key_Delete and self.state.sel_mail() is not None:
            self._delete(self.state.sel_mail()["id"])
            ev.accept()
            return
        if key == Qt.Key.Key_Escape and self._narrow and self._reading:
            self._back_to_list()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def _step(self, delta: int) -> bool:
        """Arrow keys browse without marking anything read: sweeping past a
        message is not a decision to have read it."""
        ids = [m["id"] for m in self.state.mails]
        if not ids:
            return False
        try:
            i = ids.index(self.state.selected_mail)
        except ValueError:
            i = -1 if delta > 0 else len(ids)
        j = i + delta
        if not 0 <= j < len(ids):
            return False
        self.state.select_mail_quiet(ids[j])
        row = self._rows.get(ids[j])
        if row is not None:
            self.list_scroll.ensureWidgetVisible(row, 0, 40)
        return True

    # ---- reading pane -------------------------------------------------------
    def _build_pane(self):
        st = self.state
        m = st.sel_mail()
        if not self._seen:
            sig = ("loading",)
        elif m is None:
            sig = ("none", bool(st.mails))
        else:
            sig = (m["id"], m.get("subj"), m.get("from"), m.get("date"),
                   bool(m.get("unread")),
                   tuple(m.get("label_names") or []),
                   tuple(m.get("attachments") or []),
                   self._narrow and self._reading)
        if sig != self._pane_sig:
            new_mail = (self._pane_sig or (None,))[0] != sig[0]
            self._pane_sig = sig
            clear_layout(self.pane_head)
            if not self._seen:
                for _ in range(2):
                    self.pane_head.addWidget(SkeletonRow(2, avatar=True, height=64))
            elif m is None:
                self.pane_head.addWidget(EmptyState(
                    "mail", "Nothing to read",
                    "Pick a message from the list to read it here.")
                    if st.mails else EmptyState(
                        "mail", "Nothing to read",
                        "There's no mail in this view. Write a new email "
                        "instead?", "Compose",
                        lambda: st.open_compose({})))
            else:
                self._pane_header(m)
            if new_mail:
                self.pane.verticalScrollBar().setValue(0)
        if m is None or not self._seen:
            if self._body_sig is not None:
                self._body_sig = None
                self.mail_body.clear()
            return
        raw = m.get("body_html")
        bsig = (m["id"], raw is None, len(raw or ""), len(m.get("body") or ""))
        if bsig != self._body_sig:
            self._body_sig = bsig
            self.mail_body.show_mail(m)

    def _pane_header(self, m: dict):
        v = self.pane_head
        if self._narrow and self._reading:
            back = hbox(m=(0, 0, 0, T.S4), s=0)
            back.addWidget(Button("Back to messages", "ghost", icon="arrow-left",
                                  size="sm", on_click=self._back_to_list))
            back.addStretch(1)
            v.addLayout(back)
        v.addWidget(Heading(m.get("subj", ""), 2))
        v.addSpacing(T.S4)

        who = hbox(s=T.S3)
        who.addWidget(Avatar(Avatar.initials_of(m.get("from", "")), 40), 0,
                      Qt.AlignmentFlag.AlignTop)
        col = vbox(s=2)
        name = Label(m.get("from", ""), "body", selectable=True)
        _set_weight(name, True)
        col.addWidget(name)
        meta = hbox(s=T.S2)
        if m.get("from_addr") and m.get("from_addr") != m.get("from"):
            meta.addWidget(Label(m["from_addr"], "muted", selectable=True))
        meta.addWidget(Label(m.get("date", ""), "meta"))
        if m.get("unread"):
            meta.addWidget(Badge("accent", "Unread"))
        meta.addStretch(1)
        col.addLayout(meta)
        who.addLayout(col, 1)
        v.addLayout(who)
        v.addSpacing(T.S4)

        acts_host = QWidget()
        acts = FlowLayout(acts_host, T.S2, T.S2)
        acts.addWidget(Button("Reply", "primary", icon="reply",
                              on_click=lambda: self._reply(m)))
        acts.addWidget(Button("Forward", "secondary", icon="forward",
                              on_click=lambda: self._forward(m)))
        arch = Button("Archive", "secondary", icon="archive",
                      on_click=lambda: self.state.archive_mail(m["id"]))
        arch.setToolTip("Take it out of the inbox. Lumen asks first.")
        acts.addWidget(arch)
        dele = Button("Delete", "secondary", icon="trash",
                      on_click=lambda: self._delete(m["id"]))
        dele.setToolTip("Move to Gmail Trash. Lumen asks first.")
        acts.addWidget(dele)
        if m.get("unread"):
            acts.addWidget(Button("Mark as read", "ghost", icon="eye",
                                  on_click=lambda: self._mark(m, True)))
        else:
            acts.addWidget(Button("Mark as unread", "ghost", icon="eye-off",
                                  on_click=lambda: self._mark(m, False)))
        v.addWidget(acts_host)
        v.addSpacing(T.S4)

        lab_host = QWidget()
        labs = FlowLayout(lab_host, T.S2, T.S2)
        for n in m.get("label_names") or []:
            labs.addWidget(_LabelChip(
                n, lambda name=n: self.state.remove_label(m["id"], name)))
        self._add_label_btn = Button("Add label", "ghost", icon="tag", size="sm")
        self._add_label_btn.setToolTip("Put a label on this email")
        self._add_label_btn.clicked.connect(
            lambda _=False: self._label_menu(m, self._add_label_btn))
        labs.addWidget(self._add_label_btn)
        v.addWidget(lab_host)

        attachments = m.get("attachments") or []
        if attachments:
            v.addSpacing(T.S3)
            ar = hbox(s=T.S2)
            ar.addWidget(IconLabel("paperclip", "muted", T.ICON_SM), 0,
                         Qt.AlignmentFlag.AlignTop)
            ar.addWidget(Label(", ".join(str(a) for a in attachments), "small",
                               wrap=True, selectable=True), 1)
            v.addLayout(ar)
        v.addSpacing(T.S4)
        v.addWidget(Divider())
        v.addSpacing(T.S5)

    # ---- actions ------------------------------------------------------------
    def _delete(self, mid: str):
        # External write: the daemon shows the confirm before touching Gmail.
        self.state.delete_mail(mid)

    def _mark(self, m: dict, read: bool):
        self.state.set_mail_read(m["id"], read)

    def _reply(self, m: dict):
        subj = m.get("subj", "")
        if not subj.lower().startswith("re:"):
            subj = f"Re: {subj}"
        payload = {"to": m.get("from_addr") or m.get("from", ""),
                   "subject": subj, "body": ""}
        if self.state.live:
            payload["reply_to"] = m["id"]       # threads the reply in Gmail
        self.state.open_compose(payload)

    def _forward(self, m: dict):
        subj = m.get("subj", "")
        if not subj.lower().startswith(("fwd:", "fw:")):
            subj = f"Fwd: {subj}"
        sender = m.get("from", "")
        if m.get("from_addr") and m["from_addr"] != sender:
            sender = f"{sender} <{m['from_addr']}>"
        body = ("\n\n---------- Forwarded message ----------\n"
                f"From: {sender}\nDate: {m.get('date', '')}\n"
                f"Subject: {m.get('subj', '')}\n\n{_plain_body(m)}")
        self.state.open_compose({"to": "", "subject": subj, "body": body})

    def _label_menu(self, m: dict, anchor: QWidget):
        applied = set(m.get("label_names") or [])
        menu = QMenu(self)
        for name in self.state.mail_labels:
            if name not in applied:
                menu.addAction(name, lambda n=name: self.state.apply_label(m["id"], n))
        if not menu.isEmpty():
            menu.addSeparator()
        menu.addAction("New label…", lambda: self._new_label(m))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _new_label(self, m: dict):
        if self._label_dialog is None:
            self._label_dialog = _NewLabelOverlay(self.win, self.state)
        fire_on_next_tick(lambda: self._label_dialog.open(m["id"]))

    def _suggest(self):
        if not self.state.model_enabled:
            self.busy.notice(ModelOffNotice(
                self.state, "Suggesting labels needs the model. The rest of "
                            "your inbox still works."),
                "The model is off")
            return
        self.busy.start("Sorting your inbox…",
                        "Lumen is reading unlabeled mail on this computer. "
                        "Nothing changes until you accept a suggestion.")
        self.suggest_btn.set_busy(True, "Sorting…")
        self.state.suggest_labels(cb=self._suggest_done)

    def _suggest_done(self, result):
        try:
            self.suggest_btn.set_busy(False)
        except RuntimeError:
            return
        if isinstance(result, dict) and result.get("model_off"):
            self.state._set_model_enabled(False)
            self.busy.notice(ModelOffNotice(self.state), "The model is off")
            return
        self.busy.hide()
        if self.state.mail_suggestions:
            self._open_review()
        elif self.state.live:
            self.win.show_toast("No label suggestions this time. Everything "
                                "is labeled or nothing fit.")
        else:
            self.win.show_toast("Suggesting labels needs Lumen's background "
                                "service.")
