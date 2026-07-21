"""Repeated markup from the mockup, factored into shared widget classes.

Mail rows and todo rows each appear twice in the mock (dense on Today, full on
their own screen), so both take a `compact` flag rather than being duplicated.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QWidget

from . import theme as T
from .widgets import (
    AccentBar, Chip, ClickChip, ClickLabel, ClickRow, Dot, ElideLabel,
    TodoCheck, eyebrow, font, hbox, hline, label, qcolor, vbox,
)


def section_head(title: str, meta_widget: QWidget | None = None,
                 px: float = 25, rule: bool = True) -> QWidget:
    """h2 + right-aligned meta + the 1px #d7cdb4 rule the mock puts under it."""
    w = QWidget()
    v = vbox(w, (0, 0, 0, 0), 0)
    row = hbox(m=(0, 0, 0, 12 if rule else 0), s=12)
    row.addWidget(label(title, px, T.TEXT_PRIMARY, 600, ls=-0.4))
    row.addStretch(1)
    if meta_widget is not None:
        row.addWidget(meta_widget, 0, Qt.AlignmentFlag.AlignBottom)
    v.addLayout(row)
    if rule:
        v.addWidget(hline(T.BORDER_STRONG))
    return w


def column_head(title: str, meta: str) -> QWidget:
    """The eyebrow + count pair above each Today column."""
    w = QWidget()
    row = hbox(w, (0, 0, 0, 0), 8)
    row.addWidget(eyebrow(title))
    row.addStretch(1)
    row.addWidget(label(meta, 10, T.TEXT_FAINT, mono=True))
    return w


def synced_pill(text: str, color: str = T.OK) -> QWidget:
    """'● synced 2m ago' — status dot plus mono caption."""
    w = QWidget()
    row = hbox(w, (0, 0, 0, 0), 7)
    row.addWidget(Dot(6, color))
    row.addWidget(label(text, 10, T.TEXT_MUTED, mono=True, ls=1))
    return w


def accent_fill(alpha: float | None = None) -> str:
    """QSS-safe accent tint. QSS reads #aarrggbb, so tints go through rgba()."""
    if alpha is None:
        alpha = round(T.TINT_SOFT, 3)
    r, g, b = (int(T.ACCENT[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


class MailRow(ClickRow):
    """Inbox row. `compact` is the Today-column variant: no tags, no delete,
    no selection rule, tighter padding."""

    def __init__(self, mail: dict, selected: bool = False, compact: bool = False,
                 on_click=None, on_delete=None, suggestion: tuple | None = None):
        super().__init__(on_click)
        self._selected = selected and not compact
        self._compact = compact
        self.setProperty("cls", "mailrow")

        outer = vbox(self, (0, 0, 0, 0), 0)
        inner = hbox(m=(0, 0, 0, 0), s=0)
        if not compact:
            self.bar = AccentBar(self._selected)
            inner.addWidget(self.bar)
        row = hbox(m=(0, 10, 0, 10) if compact else (16, 12, 16, 12),
                   s=10 if compact else 11)

        unread = mail.get("unread", False)
        dot = Dot(7, T.ACCENT if unread else "transparent",
                  border=None if unread else T.TEXT_GHOST)
        col = vbox(m=(0, 0, 0, 0), s=2)
        wrap = vbox(m=(0, 6, 0, 0), s=0)   # marginTop:6px on the dot
        wrap.addWidget(dot)
        wrap.addStretch(1)
        row.addLayout(wrap)

        top = hbox(m=(0, 0, 0, 0), s=8)
        top.addWidget(label(mail.get("from", ""), 12.5,
                            T.TEXT_PRIMARY if unread else T.TEXT_READ,
                            700 if unread else 500, mono=True))
        top.addStretch(1)
        top.addWidget(label(mail.get("time", ""), 9.5, T.TEXT_FAINT, mono=True))
        if on_delete is not None and not compact:
            top.addWidget(ClickLabel("✕", 11, T.TEXT_GHOST, on_click=on_delete,
                                     tooltip="Move to Trash"))
        col.addLayout(top)
        col.addWidget(ElideLabel(mail.get("subj", ""), 14 if compact else 14.5,
                                 T.TEXT_PRIMARY))
        col.addWidget(ElideLabel(mail.get("preview", ""), 11.5, T.TEXT_MUTED))

        names = mail.get("label_names") or []
        if (names or suggestion) and not compact:
            chips = hbox(m=(0, 5, 0, 0), s=4)
            for n in names[:4]:
                chips.addWidget(Chip(n, T.label_color(n), T.BORDER_MED,
                                     px=8.5, radius=3, hpad=5, vpad=1, ls=0.5))
            if suggestion is not None:
                name, accept, reject = suggestion
                chips.addWidget(ClickChip(f"✦ {name}", T.ACCENT, T.ACCENT,
                                          bg=T.ACCENT_SOFT, px=8.5, hpad=6,
                                          on_click=accept, dashed=True,
                                          tooltip=f"File under {name}"))
                chips.addWidget(ClickLabel("✕", 9, T.TEXT_GHOST,
                                           on_click=reject,
                                           tooltip="Not this label"))
            chips.addStretch(1)
            col.addLayout(chips)

        row.addLayout(col, 1)
        inner.addLayout(row, 1)
        outer.addLayout(inner)
        outer.addWidget(hline(T.BORDER_FAINT))
        self._paint_bg()

    def _paint_bg(self):
        bg = accent_fill() if self._selected else "transparent"
        self.setStyleSheet(f'QFrame[cls="mailrow"] {{ background: {bg}; }}')


class TodoRow(ClickRow):
    """Todo row. `compact` is the Today-column variant: check + text + tag only."""

    def __init__(self, todo: dict, compact: bool = False, on_toggle=None,
                 on_delete=None, on_tag=None, on_open=None):
        # Clicking the row body (not the check/tag/delete, which swallow their
        # own clicks) opens the todo's detail card (#21).
        super().__init__((lambda: on_open(todo)) if on_open else None)
        self.setProperty("cls", "todorow")
        if on_open is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        done = todo.get("done", False)

        outer = vbox(self, (0, 0, 0, 0), 0)
        row = hbox(m=(0, 9, 0, 9) if compact else (8, 9, 8, 9),
                   s=11 if compact else 10)

        check = TodoCheck(done)
        if on_toggle is not None:
            check.clicked.connect(lambda: on_toggle(todo["id"]))
        if compact:
            # align-items:flex-start — the check sits with the first text line
            wrap = vbox(m=(0, 1, 0, 0), s=0)
            wrap.addWidget(check)
            wrap.addStretch(1)
            row.addLayout(wrap)
        else:
            row.addWidget(check)

        text = label(todo.get("text", ""), 14 if compact else 14.5,
                     T.TEXT_FAINT if done else T.TEXT_PRIMARY,
                     wrap=compact, strike=done)
        row.addWidget(text, 1)

        due = todo.get("due")
        if due and not compact:
            row.addWidget(Chip(due, T.WARN, T.BORDER_DUE, px=10, radius=3,
                               hpad=7, vpad=2))
        for tag in (todo.get("tags") or [])[:2]:
            row.addWidget(ClickChip(tag, T.tag_color(tag), T.BORDER_FIELD,
                                    px=10, hpad=6, ls=0,
                                    on_click=(lambda t=tag: on_tag(t)) if on_tag else None))
        if on_delete is not None and not compact:
            row.addWidget(ClickLabel("✕", 13, T.TEXT_GHOST,
                                     on_click=lambda: on_delete(todo["id"]),
                                     tooltip="Delete"))
        outer.addLayout(row)
        outer.addWidget(hline(T.BORDER_FAINT))


class LinkRow(ClickLabel):
    """'Manage todos →' — the accent link closing each Today column."""

    def __init__(self, text: str, on_click):
        super().__init__(text, 14, T.ACCENT, on_click=on_click)


class NavRow(ClickRow):
    """Sidebar nav item: 3px accent mark, label, kbd hint or unread badge."""

    def __init__(self, key: str, text: str, meta: str, on_click):
        super().__init__(lambda: on_click(key))
        self.key = key
        self._on = False
        self.setFixedHeight(T.sc(36))
        row = hbox(self, (12, 0, 12, 0), 12)
        self.mark = Dot(3, "transparent", radius=2)
        self.mark.setFixedSize(3, T.sc(18))
        row.addWidget(self.mark)
        self.text_lab = label(text, 15, T.TEXT_SECONDARY, 500)
        row.addWidget(self.text_lab, 1)
        self.badge = Chip(meta, T.TEXT_FAINTER, None, px=11, hpad=2, vpad=0)
        row.addWidget(self.badge)
        self._restyle()

    def set_on(self, on: bool):
        self._on = on
        self._restyle()

    def set_badge(self, text: str, alert: bool):
        if alert:
            self.badge.setText(text)
            self.badge.restyle(T.ACCENT_ON, T.ACCENT, T.ACCENT)
            self.badge._radius, self.badge._hpad = 9, 6
            self.badge.setFont(font(10, 700, mono=True))
        else:
            self.badge.setText(text)
            self.badge.restyle(T.ACCENT if self._on else T.TEXT_FAINTER, None)
            self.badge._radius, self.badge._hpad = 3, 2
            self.badge.setFont(font(11, mono=True))
        self.badge.updateGeometry()

    def _restyle(self):
        self.mark.set_color(T.ACCENT if self._on else "transparent")
        pal = self.text_lab.palette()
        pal.setColor(self.text_lab.foregroundRole(),
                     qcolor(T.TEXT_PRIMARY if self._on else T.TEXT_SECONDARY))
        self.text_lab.setPalette(pal)
        self.text_lab.setFont(font(15, 600 if self._on else 500))
        if self.badge._bg is None:
            self.badge.restyle(T.ACCENT if self._on else T.TEXT_FAINTER, None)
        bg = accent_fill() if self._on else "transparent"
        self.setStyleSheet(f"NavRow {{ background: {bg}; border-radius: 5px; }}")
