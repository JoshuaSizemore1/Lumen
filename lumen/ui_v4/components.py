"""Field Notes building blocks. Screens compose these; colors come from the
theme (QSS roles in styles.py, or theme tokens for custom-painted widgets,
which repaint on `theme.manager().changed`).

Layout      vbox(host=None, m=(0,0,0,0), s=0), hbox(...), clear_layout(lay),
            repolish(w), set_prop(w, name, value), fire_on_next_tick(fn),
            shadow(w, kind="overlay"|"raised"), FlowLayout(parent, hgap, vgap)
Type        Label(text, role="body", wrap=False, selectable=False), label(...),
            Heading(text, level=1), Eyebrow(text), muted(text), small(text),
            mono(text), Kbd(text), ElideLabel(text, role="body"),
            IconLabel(name, color="fg2", size=20), TypingDots(prefix)
Controls    Button(text, variant, icon=None, size=None, on_click=None),
            IconButton(icon, tooltip, size=36, ...), TextField(label, helper,
            placeholder), TextArea(label, helper, placeholder), SearchField,
            Switch(checked, accessible_name), SwitchRow, CheckRow,
            SegmentedControl(options, value=None)
Display     Badge(kind, text), Dot(color, size), TagChip(text, slot), Avatar,
            Card, Panel, Divider, EmptyState, ClickRow, ScrollArea,
            ScreenHeader(title, subtitle), SkeletonRow, HtmlBody, MailBody,
            ModelOffNotice(state), ClaudeUnavailableNotice(state, message)
"""
from PyQt6.QtCore import (
    QEasingCurve, QPoint, QRect, QRectF, QSize, Qt, QThread, QTimer,
    QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton, QButtonGroup, QCheckBox, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLayout, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from . import icons
from . import theme as T

NO_REPLY_STATUS = "Nothing came back"
NO_REPLY_TEXT = ("Nothing came back for that — the answer never reached the "
                 "screen. Asking again usually works.")


# ---- layout helpers ---------------------------------------------------------
def hbox(host: QWidget | None = None, m=(0, 0, 0, 0), s: int = 0) -> QHBoxLayout:
    lay = QHBoxLayout(host) if host is not None else QHBoxLayout()
    lay.setContentsMargins(*m)
    lay.setSpacing(s)
    return lay


def vbox(host: QWidget | None = None, m=(0, 0, 0, 0), s: int = 0) -> QVBoxLayout:
    lay = QVBoxLayout(host) if host is not None else QVBoxLayout()
    lay.setContentsMargins(*m)
    lay.setSpacing(s)
    return lay


def clear_layout(lay) -> None:
    """Empty a layout. hide() before detaching: a visible widget with no parent
    becomes a top-level window for one tick (ui_v3 #63)."""
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def repolish(w: QWidget) -> None:
    """Re-evaluate property selectors after a setProperty()."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_prop(w: QWidget, name: str, value) -> None:
    if w.property(name) != value:
        w.setProperty(name, value)
        repolish(w)


def fire_on_next_tick(fn) -> None:
    """Run a click callback on the next event-loop turn, never inside the mouse
    event: callbacks navigate, navigation rebuilds, and deleting the widget
    whose event is being dispatched crashes Qt (ui_v3 #62)."""
    def run():
        try:
            fn()
        except RuntimeError:
            pass        # the owner went away first
    QTimer.singleShot(0, run)


def _on_theme(obj, slot) -> None:
    """Connect a bound method of a QObject to theme changes. PyQt drops the
    connection when `obj` is destroyed, so no dangling callbacks."""
    T.manager().changed.connect(slot)


class _Shadow(QGraphicsDropShadowEffect):
    def __init__(self, kind: str):
        super().__init__()
        self._kind = kind
        if kind == "raised":
            self.setBlurRadius(24)
            self.setOffset(0, 6)
        else:
            self.setBlurRadius(56)
            self.setOffset(0, 18)
        self._on_theme()
        _on_theme(self, self._on_theme)

    def _on_theme(self, *_):
        t = T.current()
        a = t.shadow_alpha if self._kind != "raised" else t.shadow_alpha // 2
        self.setColor(t.color("shadow", a))


def shadow(w: QWidget, kind: str = "overlay") -> None:
    """Overlay shadow for dialogs, raised for menus/popovers. Nothing else."""
    w.setGraphicsEffect(_Shadow(kind))


# ---- type -------------------------------------------------------------------
class Label(QLabel):
    def __init__(self, text: str = "", role: str = "body", wrap: bool = False,
                 selectable: bool = False):
        super().__init__(text)
        self.setProperty("role", role)
        self.setWordWrap(wrap)
        if selectable:
            self.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)

    def set_role(self, role: str) -> None:
        set_prop(self, "role", role)


def label(text: str = "", role: str = "body", wrap: bool = False,
          selectable: bool = False) -> Label:
    return Label(text, role, wrap, selectable)


def muted(text: str = "", wrap: bool = False) -> Label:
    return Label(text, "muted", wrap)


def small(text: str = "", wrap: bool = False) -> Label:
    return Label(text, "small", wrap)


def mono(text: str = "") -> Label:
    return Label(text, "mono")


class Heading(Label):
    """Fraunces screen/section title. level 1 = screen title (36), 2, 3."""

    def __init__(self, text: str = "", level: int = 1, wrap: bool = True):
        super().__init__(text, f"h{max(1, min(3, level))}", wrap)


def _tracked(w: QLabel) -> None:
    f = w.font()
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing,
                       100 + T.EYEBROW_TRACK * 100)
    w.setFont(f)


class Eyebrow(Label):
    """12px uppercase +0.08em label in `meta`. The only all-caps text."""

    def __init__(self, text: str = "", role: str = "eyebrow"):
        super().__init__(text.upper(), role)
        _tracked(self)

    def setText(self, text: str) -> None:
        super().setText(text.upper())


class Kbd(Label):
    def __init__(self, text: str):
        super().__init__(text, "kbd")


class ElideLabel(Label):
    """Single line that elides with an ellipsis instead of forcing width."""

    def __init__(self, text: str = "", role: str = "body", tooltip: bool = True):
        super().__init__(text, role)
        self._tip = tooltip
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)

    def minimumSizeHint(self) -> QSize:
        return QSize(24, super().minimumSizeHint().height())

    def paintEvent(self, ev):
        p = QPainter(self)
        r = self.contentsRect()
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, r.width())
        if self._tip:
            self.setToolTip(self.text() if text != self.text() else "")
        self.style().drawItemText(
            p, r, (self.alignment() | Qt.AlignmentFlag.AlignVCenter).value,
            self.palette(), self.isEnabled(), text, self.foregroundRole())


class IconLabel(QLabel):
    """A static icon that follows the theme. `color` is a token name."""

    def __init__(self, name: str, color: str = "fg2", size: int = T.ICON):
        super().__init__()
        self._name, self._color, self._size = name, color, size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._render()
        _on_theme(self, self._render)

    def set_icon(self, name: str | None = None, color: str | None = None) -> None:
        self._name = name or self._name
        self._color = color or self._color
        self._render()

    def _render(self, *_):
        self.setPixmap(icons.pixmap(self._name, self._color, self._size))


class TypingDots(Label):
    """"Thinking..." with cycling dots. The timer runs only while animating and
    never under reduced motion (power discipline)."""

    def __init__(self, prefix: str = "Thinking", role: str = "muted"):
        super().__init__(prefix, role)
        self._prefix, self._n = prefix, 1
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)

    def start(self, prefix: str | None = None) -> None:
        if prefix is not None:
            self._prefix = prefix
        self._n = 1
        if T.reduced_motion():
            self.setText(self._prefix + "…")
            return
        self.setText(self._prefix + ".")
        self._timer.start()

    def _tick(self):
        self._n = self._n % 3 + 1
        self.setText(self._prefix + "." * self._n)

    def set_static(self, text: str, role: str | None = None) -> None:
        self._timer.stop()
        if role:
            self.set_role(role)
        self.setText(text)

    def hideEvent(self, ev):
        self._timer.stop()
        super().hideEvent(ev)


# ---- buttons ----------------------------------------------------------------
_ICON_INK = {"primary": "accent_on", "danger": "accent_on", "ghost": "accent",
             "subtle": "fg2"}


class Button(QPushButton):
    """variant: primary | secondary | ghost | danger (| subtle). size: None
    (44px) or "sm" (36px). One primary per view."""

    def __init__(self, text: str = "", variant: str = "secondary",
                 icon: str | None = None, size: str | None = None,
                 on_click=None):
        super().__init__(text)
        self._icon_name = icon
        self._text = text
        self.setProperty("variant", variant)
        if size:
            self.setProperty("size", size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(True)          # Enter activates the focused button
        if on_click is not None:
            self.clicked.connect(lambda _=False: on_click())
        self._apply_icon()
        _on_theme(self, self._apply_icon)

    def _apply_icon(self, *_):
        if not self._icon_name:
            return
        px = T.ICON_SM if self.property("size") == "sm" else T.ICON
        ink = _ICON_INK.get(self.property("variant"), "fg")
        self.setIcon(icons.icon(self._icon_name, ink, px))
        self.setIconSize(QSize(px, px))

    def set_icon(self, name: str | None) -> None:
        self._icon_name = name
        if name:
            self._apply_icon()
        else:
            self.setIcon(QIcon())

    def set_variant(self, variant: str) -> None:
        set_prop(self, "variant", variant)
        self._apply_icon()

    def setText(self, text: str) -> None:
        self._text = text
        super().setText(text)

    def set_busy(self, busy: bool, busy_text: str | None = None) -> None:
        """Disable and relabel while work runs; width is held so the row
        doesn't jump."""
        if busy:
            self.setMinimumWidth(self.width())
            super().setText(busy_text or "Working…")
            self.setEnabled(False)
        else:
            super().setText(self._text)
            self.setEnabled(True)
            self.setMinimumWidth(0)


class IconButton(QPushButton):
    """Icon-only button. The tooltip doubles as the accessible name."""

    def __init__(self, icon: str, tooltip: str, size: int = T.BTN_H_SM,
                 icon_size: int = T.ICON, color: str = "fg2",
                 checkable: bool = False, variant: str | None = None,
                 on_click=None):
        super().__init__()
        self._icon_name, self._color, self._px = icon, color, icon_size
        self.setProperty("iconOnly", True)
        if variant:
            self.setProperty("variant", variant)
            if variant == "primary":
                self._color = "accent_on"
        self.setFixedSize(size, size)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCheckable(checkable)
        self.setAutoDefault(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if on_click is not None:
            self.clicked.connect(lambda _=False: on_click())
        self._apply_icon()
        _on_theme(self, self._apply_icon)

    def _apply_icon(self, *_):
        self.setIcon(icons.icon(self._icon_name, self._color, self._px))
        self.setIconSize(QSize(self._px, self._px))

    def set_icon(self, name: str, color: str | None = None) -> None:
        self._icon_name = name
        if color:
            self._color = color
        self._apply_icon()

    def set_tooltip(self, text: str) -> None:
        self.setToolTip(text)
        self.setAccessibleName(text)


# ---- inputs -----------------------------------------------------------------
class _Field(QWidget):
    """Label above, control, helper below; an error replaces the helper."""

    def __init__(self, label_text: str, control: QWidget, helper: str = ""):
        super().__init__()
        self.input = control
        self._helper = helper
        self._error = False
        v = vbox(self, (0, 0, 0, 0), 6)
        self.label = Label(label_text, "field-label")
        self.label.setBuddy(control)
        v.addWidget(self.label)
        v.addWidget(control)
        control.setAccessibleName(label_text)
        msg = QWidget()
        mrow = hbox(msg, (0, 0, 0, 0), 6)
        self._err_icon = IconLabel("alert-circle", "danger", T.ICON_SM)
        self._err_icon.hide()
        mrow.addWidget(self._err_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.message = Label(helper, "helper", wrap=True)
        mrow.addWidget(self.message, 1)
        self._msg_row = msg
        msg.setVisible(bool(helper))
        v.addWidget(msg)
        if not label_text:
            self.label.hide()

    def set_error(self, msg: str) -> None:
        self._error = True
        set_prop(self.input, "error", True)
        self.message.setText(msg)
        self.message.set_role("error")
        self._err_icon.show()
        self._msg_row.show()
        self.input.setAccessibleDescription(msg)

    def clear_error(self) -> None:
        if not self._error:
            return
        self._error = False
        set_prop(self.input, "error", False)
        self.message.setText(self._helper)
        self.message.set_role("helper")
        self._err_icon.hide()
        self._msg_row.setVisible(bool(self._helper))
        self.input.setAccessibleDescription(self._helper)

    def set_helper(self, text: str) -> None:
        self._helper = text
        if not self._error:
            self.message.setText(text)
            self._msg_row.setVisible(bool(text))

    def setFocus(self, *a):
        self.input.setFocus(*a)


class TextField(_Field):
    def __init__(self, label: str, helper: str = "", placeholder: str = "",
                 password: bool = False):
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        if password:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        super().__init__(label, edit, helper)
        edit.textEdited.connect(lambda _t: self.clear_error())

    def text(self) -> str:
        return self.input.text()

    def set_text(self, text: str) -> None:
        self.input.setText(text)


class TextArea(_Field):
    def __init__(self, label: str, helper: str = "", placeholder: str = "",
                 code: bool = False, min_height: int = 96):
        edit = QPlainTextEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumHeight(min_height)
        edit.setTabChangesFocus(True)
        if code:
            edit.setProperty("role", "code")
        super().__init__(label, edit, helper)
        edit.textChanged.connect(self.clear_error)

    def text(self) -> str:
        return self.input.toPlainText()

    def set_text(self, text: str) -> None:
        self.input.setPlainText(text)


class SearchField(QLineEdit):
    """Search input with a leading icon and clear button. `search(str)` fires
    250ms after typing settles; textChanged stays available for instant use."""

    search = pyqtSignal(str)

    def __init__(self, placeholder: str = "Search…", debounce_ms: int = 250):
        super().__init__()
        self.setProperty("role", "search")
        self.setPlaceholderText(placeholder)
        self.setAccessibleName(placeholder.rstrip("…. ") or "Search")
        self.setClearButtonEnabled(True)
        self._act = self.addAction(icons.icon("search", "muted", T.ICON_SM),
                                   QLineEdit.ActionPosition.LeadingPosition)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(lambda: self.search.emit(self.text()))
        self.textChanged.connect(lambda _t: self._timer.start())
        self.returnPressed.connect(self._now)
        _on_theme(self, self._on_theme)

    def _now(self):
        self._timer.stop()
        self.search.emit(self.text())

    def _on_theme(self, *_):
        self._act.setIcon(icons.icon("search", "muted", T.ICON_SM))


class Switch(QAbstractButton):
    """40x24 pill with a 20px knob in a 44px hit area. Slides over 220ms (none
    under reduced motion). Use `toggled(bool)`."""

    W, H = 44, 44
    PILL_W, PILL_H, KNOB = 40, 24, 20

    def __init__(self, checked: bool = False, accessible_name: str = ""):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if accessible_name:
            self.setAccessibleName(accessible_name)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self.toggled.connect(self._animate)
        _on_theme(self, self.update)

    def sizeHint(self) -> QSize:
        return QSize(self.W, self.H)

    def _step(self, v):
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool):
        end = 1.0 if on else 0.0
        dur = T.ms(T.TOGGLE_MS)
        self._anim.stop()
        if not dur or not self.isVisible():
            self._step(end)
            return
        self._anim.setDuration(dur)
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(end)
        self._anim.start()

    def set_checked_quiet(self, on: bool) -> None:
        """Reflect external state without emitting toggled."""
        was = self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(was)
        self._step(1.0 if on else 0.0)

    def paintEvent(self, ev):
        t = T.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.5)
        x0 = (self.width() - self.PILL_W) / 2
        y0 = (self.height() - self.PILL_H) / 2
        pill = QRectF(x0, y0, self.PILL_W, self.PILL_H)
        r = self.PILL_H / 2
        if self.hasFocus():
            p.setPen(QPen(t.color("accent"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(pill.adjusted(-2.5, -2.5, 2.5, 2.5), r + 2.5, r + 2.5)
        on = self._pos >= 0.5
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("accent"))
        else:
            p.setPen(QPen(t.color("border_strong"), 1))
            p.setBrush(t.color("surface_warm"))
        p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        inset = (self.PILL_H - self.KNOB) / 2
        travel = self.PILL_W - self.KNOB - 2 * inset
        kx = x0 + inset + travel * self._pos
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.color("accent_on") if on else t.color("muted"))
        p.drawEllipse(QRectF(kx, y0 + inset, self.KNOB, self.KNOB))


class SwitchRow(QWidget):
    """44px settings row: title (+ helper) left, Switch right."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, helper: str = "", checked: bool = False):
        super().__init__()
        self.setMinimumHeight(T.ROW_H)
        h = hbox(self, (0, 0, 0, 0), T.S4)
        col = vbox(s=2)
        self.title = Label(title, "body")
        col.addWidget(self.title)
        self.helper = Label(helper, "muted", wrap=True)
        self.helper.setVisible(bool(helper))
        col.addWidget(self.helper)
        h.addLayout(col, 1)
        self.switch = Switch(checked, title)
        self.switch.toggled.connect(self.toggled)
        h.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)

    def isChecked(self) -> bool:
        return self.switch.isChecked()

    def setChecked(self, on: bool) -> None:
        self.switch.setChecked(on)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self.switch.isEnabled():
            self.switch.toggle()


class CheckRow(QWidget):
    """44px row holding a labeled checkbox (+ optional helper)."""

    toggled = pyqtSignal(bool)

    def __init__(self, text: str, checked: bool = False, helper: str = ""):
        super().__init__()
        self.setMinimumHeight(T.ROW_H)
        v = vbox(self, (0, 0, 0, 0), 2)
        self.box = QCheckBox(text)
        self.box.setChecked(checked)
        self.box.toggled.connect(self.toggled)
        v.addWidget(self.box)
        if helper:
            h = Label(helper, "muted", wrap=True)
            h.setContentsMargins(28, 0, 0, 0)
            v.addWidget(h)

    def isChecked(self) -> bool:
        return self.box.isChecked()

    def setChecked(self, on: bool) -> None:
        self.box.setChecked(on)


class SegmentedControl(QFrame):
    """Exclusive segments. options: [(value, label)]. `changed(value)` fires on
    a user pick only; set_value() is quiet."""

    changed = pyqtSignal(str)

    def __init__(self, options, value: str | None = None,
                 accessible_name: str = ""):
        super().__init__()
        self.setProperty("role", "segmented")
        if accessible_name:
            self.setAccessibleName(accessible_name)
        lay = hbox(self, (3, 3, 3, 3), 2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for val, text in options:
            b = QPushButton(text)
            b.setProperty("seg", True)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=val: self._pick(v))
            self._group.addButton(b)
            self._buttons[val] = b
            lay.addWidget(b)
        self._value = None
        if value is None and options:
            value = options[0][0]
        self.set_value(value)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _pick(self, val: str):
        if val == self._value:
            return
        self._value = val
        self.changed.emit(val)

    def value(self) -> str | None:
        return self._value

    def set_value(self, val: str | None) -> None:
        b = self._buttons.get(val)
        if b is not None:
            b.setChecked(True)
            self._value = val


# ---- display ----------------------------------------------------------------
class Dot(QWidget):
    """Filled circle. `color` is a token name, a literal from theme.py, or a
    zero-arg callable returning one (e.g. lambda: T.tag_color(name))."""

    def __init__(self, color="accent", size: int = 8):
        super().__init__()
        self._color = color
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        _on_theme(self, self.update)

    def set_color(self, color) -> None:
        self._color = color
        self.update()

    def paintEvent(self, ev):
        c = self._color() if callable(self._color) else self._color
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.current().color(c))
        p.drawEllipse(QRectF(self.rect()))


class Badge(QLabel):
    """Pill with a status dot + word. kind: success | warn | danger | info |
    neutral | accent | count (count = compact number, no dot)."""

    def __init__(self, kind: str = "neutral", text: str = "", dot: bool = True):
        super().__init__(text)
        self._dot = dot and kind != "count"
        self.setProperty("role", "badge")
        self.setProperty("badge", kind)
        self.setProperty("dot", "true" if self._dot else "false")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        _on_theme(self, self.update)

    def set_kind(self, kind: str, text: str | None = None) -> None:
        if text is not None:
            self.setText(text)
        self._dot = self._dot and kind != "count"
        self.setProperty("dot", "true" if self._dot else "false")
        set_prop(self, "badge", kind)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._dot:
            return
        ink, _soft = T.current().kind(self.property("badge"))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ink))
        p.drawEllipse(QRectF(9, self.height() / 2 - 3, 6, 6))


class TagChip(QFrame):
    """Label/tag chip: small colored dot + fg2 text, 4px radius. `slot` picks
    the categorical color (int), else it's hashed from the text."""

    clicked = pyqtSignal()

    def __init__(self, text: str, slot: int | None = None, on_click=None,
                 active: bool = False):
        super().__init__()
        self.text = text
        self.setProperty("role", "tag")
        self.setProperty("active", active)
        key = slot if slot is not None else text
        h = hbox(self, (8, 3, 8, 3), 6)
        h.addWidget(Dot(lambda: T.tag_color(key), 8), 0,
                    Qt.AlignmentFlag.AlignVCenter)
        self.label = QLabel(text)
        h.addWidget(self.label)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._on_click = on_click
        if on_click is not None:
            self.setProperty("clickable", True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.setAccessibleName(text)

    def set_active(self, on: bool) -> None:
        set_prop(self, "active", on)

    def _fire(self):
        self.clicked.emit()
        if self._on_click is not None:
            fire_on_next_tick(self._on_click)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._on_click:
            self._fire()
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if self._on_click and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                           Qt.Key.Key_Space):
            self._fire()
        else:
            super().keyPressEvent(ev)


class Avatar(QWidget):
    """Initials in an accent_soft circle."""

    def __init__(self, initials: str, size: int = 36):
        super().__init__()
        self._initials = (initials or "?")[:2].upper()
        self.setFixedSize(size, size)
        _on_theme(self, self.update)

    @staticmethod
    def initials_of(name: str) -> str:
        parts = [p for p in (name or "").replace('"', "").split() if p[:1].isalnum()]
        if not parts:
            return "?"
        return (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper()

    def set_initials(self, initials: str) -> None:
        self._initials = (initials or "?")[:2].upper()
        self.update()

    def paintEvent(self, ev):
        t = T.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.color("accent_soft"))
        p.drawEllipse(QRectF(self.rect()))
        f = QFont(T.FONT_SANS)
        f.setPixelSize(max(10, int(self.height() * 0.38)))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(t.color("accent_soft_fg"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._initials)


class Card(QFrame):
    """Surface card (12 radius). `.lay` is its vbox."""

    ROLE = "card"

    def __init__(self, padding: int = T.S5, spacing: int = T.S3):
        super().__init__()
        self.setProperty("role", self.ROLE)
        self.lay = vbox(self, (padding,) * 4, spacing)


class Panel(Card):
    """Sunken panel — grouped content inside a card or dialog."""

    ROLE = "panel"


class Divider(QFrame):
    def __init__(self, vertical: bool = False):
        super().__init__()
        self.setProperty("role", "divider")
        if vertical:
            self.setFixedWidth(1)
        else:
            self.setFixedHeight(1)


class ClickRow(QFrame):
    """Hover/selected/focus row (role="row"). Click, Enter or Space fires
    `on_click` on the next tick."""

    def __init__(self, on_click=None, selected: bool = False,
                 accessible_name: str = ""):
        super().__init__()
        self.setProperty("role", "row")
        self.setProperty("selected", selected)
        self._on_click = on_click
        if on_click is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if accessible_name:
            self.setAccessibleName(accessible_name)

    def set_selected(self, on: bool) -> None:
        set_prop(self, "selected", on)

    def set_on_click(self, fn) -> None:
        self._on_click = fn

    def click(self) -> None:
        if self._on_click is not None:
            fire_on_next_tick(self._on_click)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.click()
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if self._on_click and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                           Qt.Key.Key_Space):
            self.click()
        else:
            super().keyPressEvent(ev)


class ScrollArea(QScrollArea):
    """Vertical, frameless, transparent. With no widget it makes `.body` with
    a vbox `.lay` (margins m, spacing s)."""

    def __init__(self, widget: QWidget | None = None, m=(0, 0, 0, 0), s: int = 0):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        if widget is None:
            widget = QWidget()
            self.lay = vbox(widget, m, s)
        widget.setAutoFillBackground(False)
        self.body = widget
        self.setWidget(widget)


class EmptyState(QWidget):
    """Says why it's empty + one next step."""

    def __init__(self, icon: str, title: str, body: str = "",
                 action_text: str | None = None, on_action=None,
                 action_variant: str = "secondary"):
        super().__init__()
        v = vbox(self, (T.S6, T.S8, T.S6, T.S8), T.S3)
        v.addStretch(1)
        v.addWidget(IconLabel(icon, "decor", 32), 0,
                    Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(T.S1)
        self.title = Heading(title, 3)
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self.title)
        self.body = Label(body, "muted", wrap=True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.setMaximumWidth(440)
        self.body.setVisible(bool(body))
        self.body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.body, 0, Qt.AlignmentFlag.AlignHCenter)
        self.action = None
        if action_text:
            v.addSpacing(T.S2)
            self.action = Button(action_text, action_variant, on_click=on_action)
            v.addWidget(self.action, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addStretch(1)


class ScreenHeader(QWidget):
    """H1 title + optional subtitle on the left, actions on the right."""

    def __init__(self, title: str, subtitle: str | None = None,
                 eyebrow: str | None = None):
        super().__init__()
        h = hbox(self, (0, 0, 0, 0), T.S4)
        col = vbox(s=T.S1)
        self.eyebrow = Eyebrow(eyebrow or "")
        self.eyebrow.setVisible(bool(eyebrow))
        col.addWidget(self.eyebrow)
        self.title = Heading(title, 1, wrap=False)
        col.addWidget(self.title)
        self.subtitle = Label(subtitle or "", "lead", wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        col.addWidget(self.subtitle)
        h.addLayout(col, 1)
        self.actions = hbox(s=T.S2)
        h.addLayout(self.actions)
        h.setAlignment(self.actions, Qt.AlignmentFlag.AlignBottom)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_subtitle(self, text: str | None) -> None:
        self.subtitle.setText(text or "")
        self.subtitle.setVisible(bool(text))


class SkeletonRow(QWidget):
    """Static placeholder row while data loads — no shimmer (power, motion)."""

    def __init__(self, lines: int = 2, avatar: bool = False, height: int = 56):
        super().__init__()
        self._lines, self._avatar = lines, avatar
        self.setFixedHeight(height)
        self.setAccessibleName("Loading")
        _on_theme(self, self.update)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.current().color("surface_warm"))
        x = 12
        if self._avatar:
            d = 32
            p.drawEllipse(QRectF(x, (self.height() - d) / 2, d, d))
            x += d + 12
        w = self.width() - x - 12
        n = max(1, self._lines)
        bar_h, gap = 10, 8
        y = (self.height() - (n * bar_h + (n - 1) * gap)) / 2
        for i in range(n):
            frac = (0.62, 0.9, 0.48)[i % 3]
            p.drawRoundedRect(QRectF(x, y, w * frac, bar_h), 4, 4)
            y += bar_h + gap


# ---- notices ----------------------------------------------------------------
class _Notice(QFrame):
    """Dashed-box notice; the whole box is the link to Settings."""

    def __init__(self, state, icon: str, title: str, note: str, link: str):
        super().__init__()
        self._state = state
        self.setProperty("role", "notice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{title} {link}")
        h = hbox(self, (T.S4, T.S3, T.S4, T.S3), T.S3)
        h.addWidget(IconLabel(icon, "muted"), 0, Qt.AlignmentFlag.AlignTop)
        v = vbox(s=T.S1)
        v.addWidget(Label(title, "body", wrap=True))
        if note:
            v.addWidget(Label(note, "muted", wrap=True))
        v.addWidget(Label(link, "link"))
        h.addLayout(v, 1)

    def _go(self):
        self._state.view_requested.emit("settings")
        sig = getattr(self._state, "model_switch_highlight_requested", None)
        if sig is not None:
            sig.emit()

    def click(self):
        fire_on_next_tick(self._go)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.click()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.click()
        else:
            super().keyPressEvent(ev)


class ModelOffNotice(_Notice):
    """What every AI surface shows while the model switch is off."""

    def __init__(self, state, note: str = ""):
        super().__init__(state, "power", "The local model is turned off.", note,
                         "Turn it on in Settings")


class ClaudeUnavailableNotice(_Notice):
    def __init__(self, state, message: str = ""):
        super().__init__(state, "alert-circle", "Claude is unavailable.", message,
                         "Check Settings")


# ---- mail body --------------------------------------------------------------
class HtmlBody(QTextBrowser):
    """HTML mail on a paper card (mail assumes a light page), sized to its
    document so the outer scroll area does the scrolling. No scripts, no
    remote fetches; links open externally."""

    def __init__(self, html: str):
        super().__init__()
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        f = QFont(T.FONT_SANS)
        f.setPixelSize(15)
        self.document().setDefaultFont(f)
        self._paper()
        self.setHtml(html)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _s: self._fit())
        _on_theme(self, self._paper)

    def _paper(self, *_):
        t = T.current()
        self.setStyleSheet(
            f"QTextBrowser {{ background: {t.paper}; color: {t.paper_fg}; "
            f"border: 1px solid {t.border_soft}; border-radius: {T.R_CTRL}px; "
            f"padding: 14px; }}")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.document().setTextWidth(self.viewport().width())
        self._fit()

    def _fit(self):
        h = max(int(self.document().size().height()) + 32, 48)
        if h != self.height():
            self.setFixedHeight(h)


class _ImageLoader(QThread):
    loaded = pyqtSignal(str, str)     # (message_id, inlined_html)

    def __init__(self, mid: str, html: str):
        super().__init__()
        self._mid, self._html = mid, html

    def run(self):
        from ..ui_v2 import mail_html
        self.loaded.emit(self._mid, mail_html.inline_remote_images(self._html))


class MailBody(QWidget):
    """One message's body: HTML (remote images inlined off the GUI thread, or
    click-to-load when that's switched off) or plain text. show_mail(row)
    with a full mirror row; body_html None while a fetch is in flight."""

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._loaded: dict[str, str] = {}
        self._workers: set[_ImageLoader] = set()
        self._loading: set[str] = set()
        self._m: dict | None = None
        self._lay = vbox(self, (0, 0, 0, 0), 0)

    def clear(self):
        self._m = None
        clear_layout(self._lay)

    def show_mail(self, m: dict):
        self._m = m
        self._render()

    def _render(self):
        clear_layout(self._lay)
        if self._m is not None:
            self._lay.addWidget(self._body(self._m))

    def _body(self, m: dict) -> QWidget:
        from ..ui_v2 import mail_html
        mid = m["id"]
        raw = m.get("body_html")
        if raw is None and self.state.live:
            return self._placeholder()
        if raw:
            done = self._loaded.get(mid)
            if done is not None:
                return HtmlBody(mail_html.prepare_html(done))
            hidden = mail_html.remote_image_count(raw)
            if hidden and self.state.load_remote_images:
                self._load_images(mid, raw)
                return self._placeholder()
            if hidden:
                w = QWidget()
                v = vbox(w, (0, 0, 0, 0), T.S2)
                bar = Panel(padding=T.S3)
                bar.lay.setDirection(QHBoxLayout.Direction.LeftToRight)
                bar.lay.addWidget(Label(
                    f"{hidden} remote image{'s' if hidden > 1 else ''} blocked",
                    "small"), 1)
                bar.lay.addWidget(Button(
                    "Load images", "secondary", icon="download", size="sm",
                    on_click=lambda: self._load_images(mid, raw)))
                v.addWidget(bar)
                v.addWidget(HtmlBody(mail_html.prepare_html(
                    mail_html.strip_remote_images(raw))))
                return w
            return HtmlBody(mail_html.prepare_html(raw))
        return Label(m.get("body", ""), "body", wrap=True, selectable=True)

    def _placeholder(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, T.S6, 0, 0), T.S2)
        v.addWidget(SkeletonRow(3))
        v.addWidget(Label("Loading message…", "muted"), 0,
                    Qt.AlignmentFlag.AlignHCenter)
        return w

    def _load_images(self, mid: str, html: str):
        if mid in self._loading:
            return
        self._loading.add(mid)
        worker = _ImageLoader(mid, html)
        self._workers.add(worker)
        worker.loaded.connect(self._images_ready)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        worker.start()

    def _images_ready(self, mid: str, html: str):
        self._loaded[mid] = html
        self._loading.discard(mid)
        if self._m is not None and self._m.get("id") == mid:
            self._render()


# ---- flow layout ------------------------------------------------------------
class FlowLayout(QLayout):
    """Left-aligned wrapping layout (chip rows)."""

    def __init__(self, parent=None, hgap: int = T.S2, vgap: int = T.S2):
        super().__init__(parent)
        self._items, self._h, self._v = [], hgap, vgap
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._arrange(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _arrange(self, rect, test_only: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            sz = it.sizeHint()
            if x + sz.width() > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._v, 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x += sz.width() + self._h
            line_h = max(line_h, sz.height())
        return y + line_h - rect.y()
