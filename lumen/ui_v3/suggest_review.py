"""#32 — the suggest-labels review popup: a two-pane mini inbox.

After "✦ suggest labels" classifies the inbox, this window-level overlay opens
over the whole app. LEFT: every message that got a suggested label, each with a
✓ accept / ✕ deny. RIGHT: click a row and its full body renders exactly like the
normal inbox, so the label can be judged after actually reading the mail.

Nothing was written during classification, so ✕ is purely local (drop the
suggestion) and ✓ files the mail under the suggested label — the same
emails.apply_label write a manual label uses. The popup closes once every
suggestion has been handled.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QWidget

from . import theme as T
from .components import accent_fill
from .mail_body import MailBodyView
from .overlays import _Card, _Scrim
from .widgets import (
    Avatar, Chip, ClickLabel, ClickRow, ElideLabel, button, clear_layout,
    empty_state, eyebrow, hbox, hline, label, scroll, shadow, vbox, vline,
)

LIST_W = 340
CARD_W = 860
CARD_H = 600


class SuggestReviewOverlay(_Scrim):
    def __init__(self, parent, state):
        super().__init__(parent)
        self.state = state
        self._sel: str | None = None
        self._shown: list[str] = []       # mids currently rendered in the list
        self._open = False                # own the open state — isVisible() also
        self._on_dismiss = self._dismiss  # depends on ancestors being shown

        self.card = _Card()
        self.card.setProperty("role", "dialog")
        self.card.setFixedSize(T.sc(CARD_W), T.sc(CARD_H))
        shadow(self.card, 60, 24, 80)
        outer = vbox(self.card, (0, 0, 0, 0), 0)

        head = hbox(m=(20, 15, 18, 15), s=10)
        head.addWidget(label("Review suggested labels", 15, T.TEXT_PRIMARY, 600))
        self.count = eyebrow("", T.TEXT_FAINT, 10)
        head.addWidget(self.count)
        head.addStretch(1)
        self.accept_all = button("Accept all", "soft", px=12.5, height=32)
        self.accept_all.clicked.connect(self._accept_all)
        head.addWidget(self.accept_all)
        self.dismiss_all = button("Dismiss all", "ghost", px=12.5, height=32)
        self.dismiss_all.clicked.connect(self._dismiss)
        head.addWidget(self.dismiss_all)
        head.addWidget(ClickLabel("✕", 13, T.TEXT_GHOST, on_click=self._dismiss,
                                  tooltip="Close — dismiss the rest"))
        outer.addLayout(head)
        outer.addWidget(hline(T.BORDER_MED))

        panes = hbox(m=(0, 0, 0, 0), s=0)

        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, (10, 10, 10, 10), 6)
        left = scroll(self.list_host)
        left.setFixedWidth(T.sc(LIST_W))
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panes.addWidget(left)
        panes.addWidget(vline(T.BORDER_MED))

        self.pane_host = QWidget()
        self.pane_lay = vbox(self.pane_host, (24, 20, 24, 20), 0)
        right = scroll(self.pane_host)
        panes.addWidget(right, 1)
        outer.addLayout(panes, 1)
        self.set_card(self.card)

        self.state.mails_changed.connect(self._on_mails_changed)

    # ---- open / close -----------------------------------------------------
    def open(self):
        self._sel = None
        self._shown = []
        self._open = True
        self._rebuild()
        self.pop()

    def _close(self):
        self._open = False
        self.hide()

    def _dismiss(self):
        self._close()
        self.state.dismiss_suggestions()

    def _accept_all(self):
        self._close()
        self.state.accept_all_suggestions()

    # ---- list -------------------------------------------------------------
    def _pending(self) -> list[str]:
        # Cluster same-label suggestions, then by subject — steadier to scan.
        sug = self.state.mail_suggestions
        return sorted(sug, key=lambda mid: (
            sug[mid].lower(),
            (self.state.mail_suggestion_meta.get(mid, {}).get("subj") or "").lower()))

    def _on_mails_changed(self):
        # mails_changed fires for many reasons (selection, image loads); only
        # touch the list when the set of pending suggestions actually changed,
        # so opening a message never rebuilds the list out from under the user.
        if not self._open:
            return
        if self._pending() != self._shown:
            self._rebuild()

    def _rebuild(self):
        pending = self._pending()
        self._shown = pending
        if not pending:
            self._close()
            return
        if self._sel not in pending:
            self._sel = pending[0] if pending else None

        n = len(pending)
        self.count.setText(f"{n} to review")
        self.accept_all.setEnabled(n > 0)
        clear_layout(self.list_lay)
        for mid in pending:
            self.list_lay.addWidget(self._row(mid))
        self.list_lay.addStretch(1)
        self._build_pane()

    def _row(self, mid: str) -> QWidget:
        meta = self.state.mail_suggestion_meta.get(mid, {})
        name = self.state.mail_suggestions.get(mid, "")
        selected = (mid == self._sel)
        row = ClickRow(on_click=lambda i=mid: self._select(i))
        row.setProperty("cls", "mailrow")
        bg = accent_fill() if selected else "transparent"
        row.setStyleSheet(f'QFrame[cls="mailrow"] {{ background: {bg}; }}')
        rl = hbox(row, (10, 9, 10, 9), 9)
        rl.addWidget(Avatar((meta.get("from", "?").strip() or "?")[0].upper(), 30))
        col = vbox(m=(0, 0, 0, 0), s=2)
        col.addWidget(ElideLabel(meta.get("from", "(unknown)"), 12.5,
                                 T.TEXT_PRIMARY, 600))
        col.addWidget(ElideLabel(meta.get("subj", "(no subject)"), 11.5,
                                 T.TEXT_MUTED))
        chips = hbox(m=(0, 2, 0, 0), s=6)
        chips.addWidget(Chip(name, T.label_color(name), T.BORDER_MED, px=9))
        chips.addStretch(1)
        col.addLayout(chips)
        rl.addLayout(col, 1)
        rl.addWidget(ClickLabel("✓", 15, T.OK, on_click=lambda i=mid: self._accept(i),
                                tooltip=f"File under {name}"))
        rl.addWidget(ClickLabel("✕", 14, T.TEXT_GHOST,
                                on_click=lambda i=mid: self._reject(i),
                                tooltip="Not this one"))
        return row

    def _select(self, mid: str):
        self._sel = mid
        self._rebuild_selection()

    def _rebuild_selection(self):
        # Cheap re-skin of the list rows + repaint the pane, without re-querying.
        clear_layout(self.list_lay)
        for mid in self._shown:
            self.list_lay.addWidget(self._row(mid))
        self.list_lay.addStretch(1)
        self._build_pane()

    # ---- reading pane -----------------------------------------------------
    def _build_pane(self):
        clear_layout(self.pane_lay)
        mid = self._sel
        if mid is None:
            self.pane_lay.addWidget(empty_state(
                "All caught up", "Every suggestion has been handled."))
            return
        meta = self.state.mail_suggestion_meta.get(mid, {})
        name = self.state.mail_suggestions.get(mid, "")

        self.pane_lay.addWidget(label(meta.get("subj", "(no subject)"), 20,
                                      T.TEXT_PRIMARY, 600, wrap=True))
        self.pane_lay.addSpacing(10)
        who = hbox(m=(0, 0, 0, 14), s=10)
        who.addWidget(Avatar((meta.get("from", "?").strip() or "?")[0].upper(), 34))
        wc = vbox(m=(0, 0, 0, 0), s=1)
        wc.addWidget(label(meta.get("from", ""), 13.5, T.TEXT_PRIMARY, 600))
        wc.addWidget(label(f"to me · {meta.get('date', '')}", 10.5, T.TEXT_MUTED,
                           mono=True))
        who.addLayout(wc, 1)
        self.pane_lay.addLayout(who)

        # Decide right here, after reading.
        dec = QFrame()
        dec.setProperty("role", "panel")
        dl = hbox(dec, (14, 10, 14, 10), 10)
        dl.addWidget(label("Suggested", 10.5, T.TEXT_FAINT, mono=True))
        dl.addWidget(Chip(name, T.label_color(name), T.BORDER_MED, px=10))
        dl.addStretch(1)
        acc = button(f"✓ File under {name}", "primary", px=12.5, height=32)
        acc.clicked.connect(lambda: self._accept(mid))
        dl.addWidget(acc)
        rej = button("✕ Skip", "ghost", px=12.5, height=32)
        rej.clicked.connect(lambda: self._reject(mid))
        dl.addWidget(rej)
        self.pane_lay.addWidget(dec)
        self.pane_lay.addSpacing(14)

        self.body = MailBodyView(self.state)
        self.pane_lay.addWidget(self.body, 1)
        # Meta rows carry no body_html (kept light); pull the full message now.
        self.state.fetch_mail(mid, lambda row, i=mid: self._body_ready(i, row))

    def _body_ready(self, mid: str, row: dict | None):
        # The user may have moved on (or handled this one) before the fetch
        # returned; only render if it's still the open message.
        if self._sel != mid or row is None:
            return
        self.body.show_mail(row)

    # ---- actions ----------------------------------------------------------
    def _accept(self, mid: str):
        self.state.apply_suggestion(mid)
        # In live mode apply_suggestion confirms over IPC, so mails_changed lands
        # a beat later; the suggestion is already popped, so rebuild now for an
        # instant response (the later signal is a no-op — pending == shown).
        if self._open:
            self._rebuild()

    def _reject(self, mid: str):
        self.state.reject_suggestion(mid)
        if self._open:
            self._rebuild()
