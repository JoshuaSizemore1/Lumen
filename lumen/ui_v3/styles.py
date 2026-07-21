"""One app-wide stylesheet generated from the theme tokens.

Per-widget setStyleSheet is reserved for genuinely dynamic state (a row that
flips selected, an event block tinted by its calendar color). Everything static
lives here, keyed by objectName or a `cls`/`variant` property.
"""
import re

from PyQt6.QtGui import QColor, QPalette

from . import theme as T


def app_palette() -> QPalette:
    """A complete palette built from our own tokens.

    Defensive, not a bug fix. Qt hands an app whatever palette the desktop's
    platform theme supplies (qt6ct + Kvantum + Catppuccin on this machine), and
    a widget colored only through `QPalette` — which is most of `widgets.py` —
    inherits it wherever we left a role unset. The venv's bundled Qt6 ships its
    own plugin dir and so never loads those plugins today, but that is an
    accident of packaging: a system-PyQt6 install would pick them up and repaint
    the cream shell in someone else's scheme. Owning every role, plus forcing
    Fusion in `_apply_app_style`, pins the rendering to our tokens on any
    desktop and keeps the offscreen screenshots predictive of the real window.
    """
    p = QPalette()
    R = QPalette.ColorRole
    for role, value in (
        (R.Window, T.BG_SHELL),
        (R.WindowText, T.TEXT_PRIMARY),
        (R.Base, T.BG_FIELD),
        (R.AlternateBase, T.BG_SIDEBAR),
        (R.Text, T.TEXT_PRIMARY),
        (R.PlaceholderText, T.TEXT_FAINTER),
        (R.Button, T.BG_MAIN),
        (R.ButtonText, T.TEXT_PRIMARY),
        (R.BrightText, T.ACCENT_ON),
        (R.ToolTipBase, T.BG_TOAST),
        (R.ToolTipText, "#e9e0cb"),
        (R.Light, T.BG_MAIN),
        (R.Midlight, T.BORDER_FAINT),
        (R.Mid, T.BORDER_MED),
        (R.Dark, T.BORDER_STRONG),
        (R.Shadow, T.TEXT_FAINT),
        (R.Highlight, T.ACCENT),
        (R.HighlightedText, T.ACCENT_ON),
        (R.Link, T.ACCENT),
        (R.LinkVisited, T.ACCENT_LINK_HOVER),
    ):
        p.setColor(role, QColor(value))
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(T.TEXT_FAINTER))
    return p


# Properties whose px values exist to fit text, and so track the font scale.
# Deliberately excludes `border` and `border-radius`: a hairline rule is a
# hairline at every text size, and radii are a shape signature, not a metric.
_SCALED = ("font-size", "padding", "padding-top", "padding-right",
           "padding-bottom", "padding-left", "margin", "margin-top",
           "margin-right", "margin-bottom", "margin-left", "min-height",
           "min-width", "spacing")
_DECL = re.compile(rf"\b({'|'.join(_SCALED)})\s*:\s*([^;}}]+)")
_PX = re.compile(r"(\d+(?:\.\d+)?)px")


def _scale_qss(css: str) -> str:
    """Apply the font scale to the stylesheet's text-fitting px values.

    The QSS is the one place sizes are written as text rather than passed
    through `T.sc()`, so it gets the same treatment by rewrite.
    """
    if T.FONT_SCALE == 1.0:
        return css

    def decl(m: re.Match) -> str:
        value = _PX.sub(lambda p: f"{T.sc(float(p.group(1)))}px", m.group(2))
        return f"{m.group(1)}: {value}"

    return _DECL.sub(decl, css)


def build_qss() -> str:
    return _scale_qss(f"""
/* ---------- global ---------- */
* {{
    font-family: "{T.FONT_SANS}";
    outline: none;
}}
QWidget#root {{
    background: {T.BG_SHELL};
}}
QToolTip {{
    background: {T.BG_TOAST};
    color: #e9e0cb;
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
}}

/* ---------- scrollbars (mock: 9px, #ded3ba thumb, no track) ---------- */
QScrollBar:vertical {{
    width: 9px; background: transparent; margin: 0;
}}
QScrollBar:horizontal {{
    height: 9px; background: transparent; margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: #ded3ba; border-radius: 5px; min-height: 28px; min-width: 28px;
}}
QScrollBar::handle:hover {{ background: #d0c3a6; }}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0; border: none; background: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- shell regions ---------- */
QFrame#sidebar {{
    background: {T.BG_SIDEBAR};
    border: none;
    border-right: 1px solid {T.BORDER_MED};
}}
QWidget#main, QWidget#screen {{ background: {T.BG_MAIN}; }}
QFrame#askbar {{
    background: {T.BG_SHELL};
    border: none;
    border-top: 1px solid {T.BORDER_MED};
}}
QFrame#answerPanel {{
    background: {T.BG_FIELD};
    border: none;
    border-bottom: 1px solid {T.BORDER_MED};
}}
QFrame#rail {{
    background: {T.BG_SHELL};
    border: none;
    border-left: 1px solid {T.BORDER_MED};
}}

/* ---------- panels / cards ---------- */
QFrame[role="panel"] {{
    background: {T.BG_FIELD};
    border: 1px solid {T.BORDER_FIELD};
    border-radius: 6px;
}}
QFrame[role="card"] {{
    background: {T.BG_CARD};
    border: 1px solid {T.BORDER_MED};
    border-radius: 7px;
}}
QFrame[role="folder"] {{
    background: {T.BG_SIDEBAR};
    border: 1px solid {T.BORDER_MED};
    border-radius: 7px;
}}
QFrame[role="dialog"] {{
    background: {T.BG_MAIN};
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 8px;
}}
QFrame[role="dialog-accent"] {{
    background: {T.BG_MAIN};
    border: 1px solid {T.ACCENT};
    border-radius: 8px;
}}
QFrame[role="books"] {{
    background: {T.BOOK_BG};
    border: 1px solid {T.BOOK_BORDER};
    border-radius: 6px;
}}
QFrame[role="ok"] {{
    background: #eef2e4;
    border: 1px solid {T.BORDER_OK};
    border-radius: 5px;
}}
QFrame[role="dialogfoot"] {{
    background: {T.BG_SHELL};
    border: none;
    border-top: 1px solid {T.BORDER_MED};
}}

/* ---------- buttons ---------- */
QPushButton {{
    border-radius: 4px;
    padding: 6px 14px;
    font-size: 13px;
}}
QPushButton[variant="primary"] {{
    background: {T.ACCENT};
    border: 1px solid {T.ACCENT};
    color: {T.ACCENT_ON};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {T.ACCENT_LINK_HOVER};
                                        border-color: {T.ACCENT_LINK_HOVER}; }}
QPushButton[variant="primary"]:pressed {{ background: {T.ACCENT_LINK_HOVER}; }}
QPushButton[variant="primary"]:disabled {{
    background: {T.BORDER_STRONG}; border-color: {T.BORDER_STRONG};
    color: {T.BG_MAIN};
}}

QPushButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid {T.BORDER_STRONG};
    color: {T.TEXT_SECONDARY};
}}
QPushButton[variant="ghost"]:hover {{ background: {T.BG_FIELD}; }}
QPushButton[variant="ghost"]:pressed {{ background: {T.BORDER_TRACK}; }}

QPushButton[variant="soft"] {{
    background: {T.ACCENT_SOFT_QSS};
    border: 1px solid {T.ACCENT};
    color: {T.ACCENT};
}}
QPushButton[variant="soft"]:hover {{ background: {T.ACCENT_MID_QSS}; }}

QPushButton[variant="danger"] {{
    background: transparent;
    border: 1px solid {T.BORDER_DANGER};
    color: #a24e34;
}}
QPushButton[variant="danger"]:hover {{ background: rgba(162,78,52,0.08); }}

QPushButton[variant="bare"] {{
    background: transparent; border: none; color: {T.ACCENT};
    padding: 2px 4px;
}}
QPushButton[variant="bare"]:hover {{ color: {T.ACCENT_LINK_HOVER}; }}

QPushButton[variant="books"] {{
    background: transparent;
    border: 1px solid {T.BOOK_BORDER};
    color: {T.BOOK_ACCENT};
    padding: 3px 11px;
    font-size: 13px;
}}
QPushButton[variant="books"]:hover {{ background: #ece2f6; }}

/* segmented control */
QPushButton[cls="seg"] {{
    padding: 5px 11px;
    border-radius: 4px;
    border: 1px solid {T.BORDER_STRONG};
    background: transparent;
    color: {T.TEXT_MUTED};
}}
QPushButton[cls="seg"]:checked {{
    border: 1px solid {T.ACCENT};
    background: {T.ACCENT_SOFT_QSS};
    color: {T.ACCENT};
}}
QPushButton[cls="seg"]:hover:!checked {{ background: {T.BG_FIELD}; }}

/* square icon button (‹ › ⟳) */
QPushButton[cls="icon"] {{
    background: transparent;
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 4px;
    color: {T.TEXT_SECONDARY};
    padding: 0;
}}
QPushButton[cls="icon"]:hover {{ background: {T.BG_FIELD}; }}

/* ---------- inputs ---------- */
QLineEdit {{
    background: {T.BG_FIELD};
    border: 1px solid {T.BORDER_FIELD};
    border-radius: 6px;
    color: {T.TEXT_PRIMARY};
    padding: 10px 13px;
    font-size: 14px;
    selection-background-color: {T.ACCENT_MID_QSS};
    selection-color: {T.TEXT_PRIMARY};
}}
QLineEdit:focus {{ border: 1px solid {T.ACCENT}; }}
QLineEdit[cls="bare"] {{
    background: transparent; border: none; padding: 0;
}}
QLineEdit[cls="bare"]:focus {{ border: none; }}
QLineEdit[cls="onpanel"] {{
    background: {T.BG_INPUT};
    border: 1px solid {T.BORDER_INPUT};
    border-radius: 4px;
    padding: 8px 10px;
    font-size: 13px;
}}
QLineEdit:disabled {{ color: {T.TEXT_FAINTER}; }}

QPlainTextEdit, QTextEdit {{
    background: {T.BG_FIELD};
    border: 1px solid {T.BORDER_FIELD};
    border-radius: 6px;
    color: {T.TEXT_PRIMARY};
    padding: 11px 13px;
    font-size: 14px;
    selection-background-color: {T.ACCENT_MID_QSS};
    selection-color: {T.TEXT_PRIMARY};
}}
QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {T.ACCENT}; }}
QPlainTextEdit[cls="editor"] {{
    background: {T.BG_MAIN};
    border: none;
    border-radius: 0;
    color: {T.TEXT_BODY};
    font-family: "{T.FONT_MONO}";
    font-size: 13px;
    padding: 18px 30px;
}}

QComboBox {{
    background: {T.BG_FIELD};
    border: 1px solid {T.BORDER_FIELD};
    border-radius: 6px;
    color: {T.TEXT_PRIMARY};
    padding: 8px 10px;
    font-size: 13px;
}}
QComboBox:focus {{ border: 1px solid {T.ACCENT}; }}
QComboBox[cls="onpanel"] {{
    background: {T.BG_INPUT};
    border: 1px solid {T.BORDER_INPUT};
    border-radius: 4px;
}}
/* The chevron is painted by widgets.Select — QSS has no glyph primitive and
   the native arrow clashes with the cream palette. Reserve the space here. */
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ border: none; width: 0; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background: {T.BG_MAIN};
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 5px;
    color: {T.TEXT_PRIMARY};
    selection-background-color: {T.ACCENT_SOFT_QSS};
    selection-color: {T.TEXT_PRIMARY};
    padding: 3px;
    outline: none;
}}

QCheckBox {{ color: {T.TEXT_SECONDARY}; font-size: 13px; spacing: 8px; }}
QCheckBox::indicator {{
    width: {T.sc(15)}px; height: {T.sc(15)}px; border-radius: 3px;
    border: 1px solid {T.TEXT_GHOST}; background: transparent;
}}
QCheckBox::indicator:checked {{
    background: {T.ACCENT}; border: 1px solid {T.ACCENT};
}}

/* ---------- misc ---------- */
QFrame#toast {{
    background: {T.BG_TOAST};
    border: 1px solid {T.OK};
    border-radius: 7px;
}}
QWidget#scrim {{ background: {T.BG_SCRIM}; }}
QWidget#scrimLaunch {{ background: {T.BG_SCRIM_LAUNCH}; }}
QSplitter::handle {{ background: {T.BORDER_MED}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
""")
