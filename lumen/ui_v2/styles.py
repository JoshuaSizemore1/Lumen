"""App-wide QSS generated from theme tokens.

Containers, buttons, inputs and scrollbars are styled here via objectName /
dynamic-property selectors. Pure text styling (size/color/weight per label)
is done in code via widgets.label() for exact px control.
"""
from . import theme as T


def build_qss() -> str:
    return f"""
/* App-wide family comes from QApplication.setFont (QSS font-family here
   would override the per-label QFont and kill the sans prose font). */
* {{ outline: none; }}

QWidget#root {{ background: {T.BG_WINDOW}; }}
QWidget#titlebar {{ background: {T.BG_CHROME}; border-bottom: 1px solid {T.BORDER_SOFT}; }}
QWidget#tabbar {{ background: {T.BG_CHROME}; border-bottom: 1px solid {T.BORDER_SOFT}; }}

/* ---- scrollbars (web-style thin) ---- */
QScrollArea {{ background: transparent; border: none; }}
QAbstractScrollArea::corner {{ background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 0; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; margin: 0; }}
QScrollBar::handle {{ background: {T.BORDER_STRONG}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- tab bar ---- */
QPushButton[cls="tab"] {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 0 13px;
    font-size: 12px;
    color: {T.TEXT_DIM};
}}
QPushButton[cls="tab"]:checked {{
    color: {T.TEXT_PRIMARY};
    border-bottom: 2px solid {T.ACCENT};
}}
QPushButton[cls="tabgear"] {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 15px;
    color: {T.TEXT_DIM};
}}
QPushButton[cls="tabgear"]:checked {{
    color: {T.ACCENT};
    border-bottom: 2px solid {T.ACCENT};
}}

/* ---- segmented controls ---- */
QPushButton[cls="seg"] {{
    padding: 4px 10px;
    font-size: 11px;
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 5px;
    background: transparent;
    color: {T.TEXT_DIM};
}}
QPushButton[cls="seg"]:checked {{
    color: {T.TEXT_PRIMARY};
    background: {T.ACCENT_SOFT_QSS};
    border: 1px solid {T.ACCENT};
}}

/* ---- button variants ---- */
QPushButton[variant="primary"] {{
    background: {T.ACCENT};
    border: 1px solid {T.ACCENT};
    border-radius: 6px;
    color: {T.ACCENT_ON};
    font-weight: 600;
    font-size: 11px;
    padding: 5px 14px;
}}
QPushButton[variant="outline"] {{
    background: transparent;
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 6px;
    color: {T.TEXT_SECONDARY};
    font-size: 11px;
    padding: 5px 12px;
}}
QPushButton[variant="soft"] {{
    background: {T.ACCENT_SOFT_QSS};
    border: 1px solid {T.ACCENT};
    border-radius: 5px;
    color: {T.TEXT_PRIMARY};
    font-size: 11px;
    padding: 5px 12px;
}}
QPushButton[variant="ghost-accent"] {{
    background: transparent;
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 6px;
    color: {T.ACCENT};
    font-size: 11px;
    padding: 5px 10px;
}}
QPushButton[variant="ghost-blue"] {{
    background: transparent;
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 5px;
    color: #7aa2f7;
    font-size: 11px;
    padding: 5px 10px;
}}
QPushButton[square="true"] {{ padding: 0; }}
QPushButton[variant="rec"] {{
    background: transparent;
    border: 1px solid {T.BOOK_BORDER};
    border-radius: 4px;
    color: {T.BOOK};
    font-size: 10px;
    padding: 3px 9px;
}}
QPushButton[variant="cancel"] {{
    background: transparent;
    border: 1px solid {T.TEXT_FAINT};
    border-radius: 7px;
    color: {T.TEXT_SECONDARY};
    font-size: 13px;
    padding: 9px;
}}
QPushButton[variant="confirm"] {{
    background: {T.ACCENT};
    border: 1px solid {T.ACCENT};
    border-radius: 7px;
    color: {T.ACCENT_ON};
    font-size: 13px;
    font-weight: 600;
    padding: 9px;
}}

/* ---- inputs ---- */
QLineEdit {{
    background: {T.BG_FIELD};
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 5px;
    color: {T.TEXT_PRIMARY};
    font-size: 12px;
    padding: 7px 9px;
    selection-background-color: {T.ACCENT_MID_QSS};
    placeholder-text-color: {T.TEXT_DIM};
}}
QLineEdit[cls="bare"] {{
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
}}
QComboBox {{
    background: {T.BG_FIELD};
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 5px;
    color: {T.WARN};
    font-size: 12px;
    padding: 7px 6px;
}}
QComboBox::drop-down {{ border: none; width: 12px; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{
    background: {T.BG_PANEL};
    border: 1px solid {T.BORDER_STRONG};
    color: {T.WARN};
    selection-background-color: {T.ACCENT_MID_QSS};
}}

/* ---- launcher palette ---- */
QFrame[cls="palette"] {{
    background: {T.BG_PANEL};
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 10px;
}}
QFrame[cls="bar-top"] {{
    background: {T.BG_PANEL_ALT};
    border-bottom: 1px solid {T.BORDER_SOFT};
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
}}
QFrame[cls="bar-bottom"] {{
    background: {T.BG_PANEL_ALT};
    border-top: 1px solid {T.BORDER_SOFT};
    border-bottom-left-radius: 9px;
    border-bottom-right-radius: 9px;
}}

/* ---- highlightable rows (selection = left accent bar + soft fill) ---- */
QFrame[cls="selrow"] {{ border-left: 2px solid transparent; }}
QFrame[cls="selrow"][sel="true"] {{
    background: {T.ACCENT_SOFT_QSS};
    border-left: 2px solid {T.ACCENT};
}}
QFrame[cls="evrow"] {{ border-left: 2px solid transparent; }}
QFrame[cls="evrow"][next="true"] {{
    background: {T.ACCENT_SOFT_QSS};
    border-left: 2px solid {T.ACCENT};
}}
QFrame[cls="dayhead"] {{ border-left: 1px solid {T.BORDER_FAINT}; }}
QFrame[cls="dayhead"][today="true"] {{ background: {T.ACCENT_SOFT_QSS}; }}
QFrame#waybar {{
    background: {T.BG_WAYBAR};
    border-bottom: 1px solid {T.BORDER_WAYBAR};
}}
QFrame#todocard {{
    background: #131820;
    border: 1px solid #24313a;
    border-radius: 7px;
}}

/* ---- panels / frames ---- */
QFrame[cls="panel"] {{
    background: {T.BG_PANEL};
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 8px;
}}
QFrame[cls="inset"] {{
    background: {T.BG_INSET};
    border: 1px solid {T.BORDER_MED};
    border-radius: 7px;
}}
QFrame[cls="recpanel"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {T.BOOK_GRAD_TOP}, stop:1 {T.BOOK_GRAD_BOT});
    border: 1px dashed {T.BOOK_BORDER};
    border-radius: 9px;
}}
QFrame[cls="dialog"] {{
    background: {T.BG_DIALOG};
    border: 1px solid {T.ACCENT};
    border-radius: 11px;
}}
QToolTip {{
    background: {T.BG_DIALOG};
    color: {T.TEXT_SECONDARY};
    border: 1px solid {T.BORDER_STRONG};
    font-size: 11px;
}}
"""
