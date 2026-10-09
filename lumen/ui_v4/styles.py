"""App stylesheet + palette, generated from a Theme.

Everything is keyed off objectName or a dynamic property, never off a widget's
own inline setStyleSheet, so a theme switch is one app.setStyleSheet() call.
Properties screens can set (then `components.repolish(w)`):

  QPushButton[variant="primary|secondary|ghost|danger|subtle"][size="sm"]
  QPushButton[iconOnly="true"]      QPushButton[seg="true"] (in a segmented)
  QLabel[role="h1|h2|h3|lead|body|small|muted|meta|eyebrow|mono|number|
               field-label|helper|error|link|kbd|caption"]
  QLabel[role="badge"][badge="success|warn|danger|info|neutral|accent|count"]
  QFrame[role="card|panel|inset|divider|row|tag|segmented|dialog-foot|toolbar"]
  QFrame[selected="true"] on rows, QFrame[active="true"] on tags
  QLineEdit[role="bare|search"], [error="true"], [size="sm"]
"""
from PyQt6.QtGui import QColor, QPalette

from . import icons
from . import theme as T


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def build_qss(t: T.Theme | None = None) -> str:
    t = t or T.current()
    sans, disp, mono = T.FONT_SANS, T.FONT_DISPLAY, T.FONT_MONO
    chev = icons.icon_path("chevron-down", t.muted, 16)
    chev_up = icons.icon_path("chevron-up", t.muted, 16)
    tick = icons.icon_path("check", t.accent_on, 16)
    dash = icons.icon_path("minus", t.accent_on, 16)
    r = T.R_CTRL

    return f"""
/* ---- grounds ------------------------------------------------------- */
QWidget {{ color: {t.fg}; selection-background-color: {t.selection};
          selection-color: {t.fg}; }}
QWidget#root, QWidget#content, QStackedWidget#stack {{ background: {t.bg}; }}
QFrame#sidebar {{ background: {t.surface}; border: none;
                 border-right: 1px solid {t.border_soft}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > #qt_scrollarea_viewport {{ background: transparent; }}

/* ---- type ---------------------------------------------------------- */
QLabel {{ background: transparent; }}
QLabel[role="h1"] {{ font-family: "{disp}"; font-size: {T.H1}px; font-weight: 500;
                    color: {t.fg}; }}
QLabel[role="h2"] {{ font-family: "{disp}"; font-size: {T.H2}px; font-weight: 500;
                    color: {t.fg}; }}
QLabel[role="h3"] {{ font-family: "{disp}"; font-size: {T.H3}px; font-weight: 500;
                    color: {t.fg}; }}
QLabel[role="number"] {{ font-family: "{disp}"; font-size: {T.H2}px;
                        font-weight: 500; color: {t.fg}; }}
QLabel[role="lead"] {{ font-size: {T.LEAD}px; color: {t.fg2}; }}
QLabel[role="body"] {{ font-size: {T.BODY}px; color: {t.fg}; }}
QLabel[role="small"] {{ font-size: {T.SMALL}px; color: {t.fg2}; }}
QLabel[role="muted"] {{ font-size: {T.SMALL}px; color: {t.muted}; }}
QLabel[role="caption"] {{ font-size: {T.EYEBROW}px; color: {t.muted}; }}
QLabel[role="meta"] {{ font-family: "{mono}"; font-size: 12px; color: {t.muted}; }}
QLabel[role="mono"] {{ font-family: "{mono}"; font-size: 13px; color: {t.fg2}; }}
QLabel[role="eyebrow"] {{ font-size: {T.EYEBROW}px; font-weight: 600;
                         color: {t.meta}; }}
QLabel[role="field-label"] {{ font-size: {T.SMALL}px; font-weight: 500;
                             color: {t.fg}; }}
QLabel[role="helper"] {{ font-size: 13px; color: {t.muted}; }}
QLabel[role="error"] {{ font-size: 13px; color: {t.danger}; }}
QLabel[role="link"] {{ font-size: {T.SMALL}px; font-weight: 500; color: {t.accent}; }}
QLabel[role="link"]:hover {{ color: {t.accent_hover}; }}
QLabel[role="kbd"] {{ font-family: "{mono}"; font-size: 11px; color: {t.muted};
                     border: 1px solid {t.border}; border-radius: {T.R_TAG}px;
                     padding: 0px 5px; background: {t.surface}; }}
QLabel#brand {{ font-family: "{disp}"; font-size: 24px; font-weight: 500;
               color: {t.fg}; }}
QLabel#brandCaption {{ font-size: 12px; color: {t.muted}; }}

/* ---- badges -------------------------------------------------------- */
QLabel[role="badge"] {{ border-radius: 10px; padding: 2px 9px 2px 20px;
                       font-size: 12px; font-weight: 600; }}
QLabel[role="badge"][dot="false"] {{ padding: 2px 9px; }}
QLabel[badge="success"] {{ background: {t.success_soft}; color: {t.success}; }}
QLabel[badge="warn"] {{ background: {t.warn_soft}; color: {t.warn}; }}
QLabel[badge="danger"] {{ background: {t.danger_soft}; color: {t.danger}; }}
QLabel[badge="info"] {{ background: {t.info_soft}; color: {t.info}; }}
QLabel[badge="neutral"] {{ background: {t.surface_warm}; color: {t.fg2}; }}
QLabel[badge="accent"] {{ background: {t.accent_soft}; color: {t.accent_soft_fg}; }}
QLabel[role="badge"][badge="count"] {{ background: {t.accent}; color: {t.accent_on};
                        font-family: "{mono}"; font-size: 11px; font-weight: 600;
                        padding: 1px 7px; border-radius: 9px; }}

/* ---- frames -------------------------------------------------------- */
QFrame[role="card"] {{ background: {t.surface}; border: 1px solid {t.border_soft};
                      border-radius: {T.R_CARD}px; }}
QFrame[role="panel"] {{ background: {t.surface_sunken}; border: none;
                       border-radius: {T.R_CARD}px; }}
QFrame[role="inset"] {{ background: {t.bg}; border: 1px solid {t.border_soft};
                       border-radius: {r}px; }}
QFrame[role="divider"] {{ background: {t.border_soft}; border: none; }}
QFrame[role="toolbar"] {{ background: transparent; border: none;
                         border-bottom: 1px solid {t.border_soft}; }}
QFrame[role="row"] {{ background: transparent; border: none; border-radius: {r}px; }}
QFrame[role="row"]:hover {{ background: {t.surface_warm}; }}
QFrame[role="row"][selected="true"] {{ background: {t.accent_soft}; }}
QFrame[role="row"][selected="true"] QLabel {{ color: {t.accent_soft_fg}; }}
QFrame[role="row"][selected="true"] QLabel[role="muted"],
QFrame[role="row"][selected="true"] QLabel[role="meta"] {{ color: {t.fg2}; }}
QFrame[role="row"]:focus {{ border: 2px solid {t.accent}; }}
QFrame[role="tag"] {{ background: {t.surface}; border: 1px solid {t.border};
                     border-radius: {T.R_TAG}px; }}
QFrame[role="tag"] QLabel {{ color: {t.fg2}; font-size: 12px; }}
QFrame[role="tag"][clickable="true"]:hover {{ background: {t.surface_warm}; }}
QFrame[role="tag"][active="true"] {{ background: {t.accent_soft};
                                    border-color: {t.accent}; }}
QFrame[role="tag"][active="true"] QLabel {{ color: {t.accent_soft_fg}; }}
QFrame[role="notice"] {{ background: {t.surface_sunken};
                        border: 1px dashed {t.border_strong};
                        border-radius: {r}px; }}
QFrame[role="notice"]:hover {{ border-color: {t.accent}; }}

/* ---- sidebar ------------------------------------------------------- */
QFrame[role="nav"] {{ background: transparent; border-radius: {r}px; }}
QFrame[role="nav"]:hover {{ background: {t.surface_warm}; }}
QFrame[role="nav"][selected="true"] {{ background: {t.accent_soft}; }}
QFrame[role="nav"]:focus {{ border: 2px solid {t.accent}; }}
QLabel[role="nav-label"] {{ font-size: 15px; font-weight: 500; color: {t.fg2}; }}
QFrame[role="nav"][selected="true"] QLabel[role="nav-label"] {{
    color: {t.accent_soft_fg}; font-weight: 600; }}
QLabel[role="nav-count"] {{ font-family: "{mono}"; font-size: 12px; color: {t.muted}; }}
QLabel[role="nav-eyebrow"] {{ font-size: 12px; font-weight: 600; color: {t.meta}; }}

/* ---- buttons ------------------------------------------------------- */
QPushButton {{ font-size: 15px; font-weight: 500; border-radius: {r}px;
              padding: 0px 16px; min-height: {T.BTN_H - 2}px;
              background: {t.surface}; color: {t.fg};
              border: 1px solid {t.border_strong}; }}
QPushButton:hover {{ background: {t.surface_warm}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:focus {{ border: 2px solid {t.accent}; padding: 0px 15px; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.surface_warm};
                       border-color: {t.border_soft}; }}
QPushButton[size="sm"] {{ font-size: {T.SMALL}px; min-height: {T.BTN_H_SM - 2}px;
                         padding: 0px 12px; }}
QPushButton[size="sm"]:focus {{ padding: 0px 11px; }}
QPushButton[variant="primary"] {{ background: {t.accent}; color: {t.accent_on};
                                 border: 1px solid {t.accent}; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {t.accent_hover};
                                       border-color: {t.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {t.accent_active};
                                         border-color: {t.accent_active}; }}
QPushButton[variant="primary"]:focus {{ border: 2px solid {t.accent_active}; }}
QPushButton[variant="primary"]:disabled {{ background: {t.surface_warm};
                                          color: {t.muted};
                                          border-color: {t.border_soft}; }}
QPushButton[variant="danger"] {{ background: {t.danger}; color: {t.accent_on};
                                border: 1px solid {t.danger}; font-weight: 600; }}
QPushButton[variant="danger"]:hover {{ background: {t.fg if not t.dark else t.danger_soft};
                                      color: {t.bg if not t.dark else t.danger};
                                      border-color: {t.danger}; }}
QPushButton[variant="danger"]:focus {{ border: 2px solid {t.fg}; }}
QPushButton[variant="ghost"] {{ background: transparent; color: {t.accent};
                               border: 1px solid transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {t.accent_soft};
                                     color: {t.accent_soft_fg}; }}
QPushButton[variant="ghost"]:focus {{ border: 2px solid {t.accent}; }}
QPushButton[variant="ghost"]:disabled {{ background: transparent; color: {t.muted}; }}
QPushButton[variant="subtle"] {{ background: transparent; color: {t.fg2};
                                border: 1px solid transparent; }}
QPushButton[variant="subtle"]:hover {{ background: {t.surface_warm}; color: {t.fg}; }}
QPushButton[iconOnly="true"] {{ background: transparent; border: 1px solid transparent;
                               padding: 0px; min-height: 0px; }}
QPushButton[iconOnly="true"]:hover {{ background: {t.surface_warm}; }}
QPushButton[iconOnly="true"]:pressed {{ background: {t.border}; }}
QPushButton[iconOnly="true"]:focus {{ border: 2px solid {t.accent}; padding: 0px; }}
QPushButton[iconOnly="true"]:checked {{ background: {t.accent_soft}; }}
QPushButton[iconOnly="true"][variant="primary"] {{ background: {t.accent};
                                                 border-color: {t.accent}; }}
QPushButton[iconOnly="true"][variant="primary"]:hover {{
    background: {t.accent_hover}; }}

QFrame[role="segmented"] {{ background: {t.surface_warm}; border-radius: {r}px;
                           border: none; }}
QPushButton[seg="true"] {{ background: transparent; border: 1px solid transparent;
                          border-radius: 6px; color: {t.fg2}; font-size: {T.SMALL}px;
                          font-weight: 500; min-height: 28px; padding: 0px 12px; }}
QPushButton[seg="true"]:hover {{ color: {t.fg}; background: {t.border_soft}; }}
QPushButton[seg="true"]:checked {{ background: {t.surface}; color: {t.fg};
                                  border: 1px solid {t.border}; }}
QPushButton[seg="true"]:focus {{ border: 2px solid {t.accent}; padding: 0px 11px; }}

/* ---- inputs -------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
    background: {t.surface}; color: {t.fg}; font-size: 15px;
    border: 1px solid {t.border_strong}; border-radius: {r}px;
    padding: 8px 12px; min-height: 26px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDateEdit:hover, QTimeEdit:hover {{
    border-color: {t.muted}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
    border: 2px solid {t.accent}; padding: 7px 11px; }}
QLineEdit:disabled, QComboBox:disabled {{ background: {t.surface_warm};
                                         color: {t.muted}; }}
QLineEdit[size="sm"], QComboBox[size="sm"] {{ min-height: 18px; font-size: {T.SMALL}px; }}
QLineEdit[error="true"], QPlainTextEdit[error="true"], QTextEdit[error="true"] {{
    border: 2px solid {t.danger}; padding: 7px 11px; }}
QLineEdit[role="bare"] {{ background: transparent; border: none; padding: 4px 0px;
                         min-height: 24px; }}
QLineEdit[role="bare"]:focus {{ border: none; padding: 4px 0px; }}
QLineEdit[role="search"] {{ padding-left: 6px; }}
QLineEdit[role="search"]:focus {{ padding-left: 5px; }}
QPlainTextEdit, QTextEdit {{ background: {t.surface}; color: {t.fg}; font-size: 15px;
                            border: 1px solid {t.border_strong}; border-radius: {r}px;
                            padding: 8px 10px; }}
QPlainTextEdit:focus, QTextEdit:focus {{ border: 2px solid {t.accent};
                                        padding: 7px 9px; }}
QPlainTextEdit[role="bare"], QTextEdit[role="bare"], QTextBrowser[role="bare"] {{
    background: transparent; border: none; padding: 0px; }}
QPlainTextEdit[role="code"] {{ font-family: "{mono}"; font-size: 13px; }}
QTextBrowser {{ background: transparent; border: none; }}

QComboBox {{ padding-right: 32px; }}
QComboBox:focus {{ padding-right: 31px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: right center;
                       width: 28px; border: none; }}
QComboBox::down-arrow {{ image: url("{chev}"); width: 16px; height: 16px; }}
QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.fg};
                              border: 1px solid {t.border}; padding: 4px;
                              outline: none;
                              selection-background-color: {t.accent_soft};
                              selection-color: {t.accent_soft_fg}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button, QDateEdit::up-button, QDateEdit::down-button,
QTimeEdit::up-button, QTimeEdit::down-button {{ border: none; width: 20px;
                                               background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateEdit::up-arrow,
QTimeEdit::up-arrow {{ image: url("{chev_up}"); width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateEdit::down-arrow,
QTimeEdit::down-arrow {{ image: url("{chev}"); width: 12px; height: 12px; }}

QCheckBox, QRadioButton {{ spacing: 10px; font-size: 15px; color: {t.fg};
                          min-height: {T.ROW_H - 20}px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px;
    border: 1px solid {t.border_strong}; background: {t.surface}; }}
QCheckBox::indicator {{ border-radius: {T.R_TAG}px; }}
QRadioButton::indicator {{ border-radius: 10px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent};
                               image: url("{tick}"); }}
QCheckBox::indicator:indeterminate {{ background: {t.accent}; border-color: {t.accent};
                                     image: url("{dash}"); }}
QRadioButton::indicator:checked {{ background: {t.surface};
                                  border: 6px solid {t.accent}; }}
QCheckBox:focus, QRadioButton:focus {{ color: {t.accent_hover}; }}

QSlider::groove:horizontal {{ height: 4px; background: {t.surface_warm};
                             border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0px;
                             border-radius: 8px; background: {t.accent}; }}

QProgressBar {{ background: {t.surface_warm}; border: none; border-radius: 3px;
               max-height: 6px; min-height: 6px; text-align: center;
               color: transparent; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}

/* ---- scroll bars --------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.border_strong}; border-radius: 3px;
                              min-height: 32px; margin: 0px 1px; }}
QScrollBar::handle:horizontal {{ background: {t.border_strong}; border-radius: 3px;
                                min-width: 32px; margin: 1px 0px; }}
QScrollBar::handle:hover {{ background: {t.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- item views ---------------------------------------------------- */
QListView, QTreeView, QTableView, QListWidget, QTreeWidget, QTableWidget {{
    background: transparent; border: none; outline: none; font-size: 15px;
    alternate-background-color: {t.surface}; }}
QListView::item, QTreeView::item, QListWidget::item, QTreeWidget::item {{
    padding: 8px 10px; border-radius: 6px; color: {t.fg}; }}
QTableView::item {{ padding: 6px 10px; }}
QListView::item:hover, QTreeView::item:hover, QTableView::item:hover {{
    background: {t.surface_warm}; }}
QListView::item:selected, QTreeView::item:selected, QTableView::item:selected {{
    background: {t.accent_soft}; color: {t.accent_soft_fg}; }}
QTreeView::branch {{ background: transparent; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: transparent; color: {t.muted}; font-size: 12px;
                       font-weight: 600; border: none;
                       border-bottom: 1px solid {t.border_soft}; padding: 6px 10px; }}
QTableView {{ gridline-color: {t.border_soft}; }}
QSplitter::handle {{ background: {t.border_soft}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* ---- tabs ---------------------------------------------------------- */
QTabWidget::pane {{ border: none; border-top: 1px solid {t.border_soft}; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {t.muted}; font-size: 15px;
               font-weight: 500; padding: 10px 2px; margin-right: 20px;
               border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {t.fg}; }}
QTabBar::tab:selected {{ color: {t.fg}; border-bottom: 2px solid {t.accent}; }}

/* ---- menus / tooltips ---------------------------------------------- */
QMenu {{ background: {t.surface}; color: {t.fg}; border: 1px solid {t.border};
        padding: 6px; font-size: {T.SMALL}px; }}
QMenu::item {{ padding: 8px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.surface_warm}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.border_soft}; margin: 5px 6px; }}
QMenu::icon {{ padding-left: 6px; }}
QToolTip {{ background: {t.toast_bg}; color: {t.toast_fg}; border: none;
           padding: 6px 10px; font-size: 13px; }}

/* ---- overlays ------------------------------------------------------ */
QWidget#scrim {{ background: {t.scrim}; }}
QWidget#scrimTop {{ background: {t.scrim}; }}
QFrame#dialog {{ background: {t.surface}; border: 1px solid {t.border_soft};
                border-radius: {T.R_CARD}px; }}
QFrame#palette {{ background: {t.surface}; border: 1px solid {t.border};
                 border-radius: {T.R_CARD}px; }}
QFrame[role="dialog-foot"] {{ background: {t.bg}; border: none;
                             border-top: 1px solid {t.border_soft};
                             border-bottom-left-radius: {T.R_CARD}px;
                             border-bottom-right-radius: {T.R_CARD}px; }}
QFrame#toast {{ background: {t.toast_bg}; border-radius: 22px; }}
QLabel#toastText {{ color: {t.toast_fg}; font-size: {T.SMALL}px; font-weight: 500; }}
QPushButton#toastAction {{ background: transparent; color: {t.toast_fg};
                          border: 1px solid {_rgba(t.toast_fg, 0.45)};
                          border-radius: 14px; min-height: 26px; padding: 0px 12px;
                          font-size: 13px; font-weight: 600; }}
QPushButton#toastAction:hover {{ background: {_rgba(t.toast_fg, 0.14)}; }}
QListWidget#paletteList::item {{ padding: 10px 12px; border-radius: {r}px; }}
QListWidget#paletteList::item:selected {{ background: {t.accent_soft};
                                         color: {t.accent_soft_fg}; }}

/* ---- ask bar ------------------------------------------------------- */
QFrame#askbar {{ background: {t.bg}; border: none;
                border-top: 1px solid {t.border_soft}; }}
QFrame#askField {{ background: {t.surface}; border: 1px solid {t.border_strong};
                  border-radius: {r}px; }}
QFrame#askField[focused="true"] {{ border: 2px solid {t.accent}; }}
QLabel#askLabel {{ font-size: 13px; font-weight: 600; color: {t.meta}; }}
QFrame#statusLine {{ background: transparent; }}
"""


def app_palette(t: T.Theme | None = None) -> QPalette:
    t = t or T.current()
    R = QPalette.ColorRole
    G = QPalette.ColorGroup
    pal = QPalette()
    c = QColor
    for role, value in (
            (R.Window, t.bg), (R.WindowText, t.fg), (R.Base, t.surface),
            (R.AlternateBase, t.surface_warm), (R.Text, t.fg),
            (R.Button, t.surface), (R.ButtonText, t.fg),
            (R.BrightText, t.accent_on), (R.Highlight, t.accent_soft),
            (R.HighlightedText, t.accent_soft_fg), (R.ToolTipBase, t.toast_bg),
            (R.ToolTipText, t.toast_fg), (R.PlaceholderText, t.muted),
            (R.Link, t.accent), (R.LinkVisited, t.accent_hover),
            (R.Light, t.surface), (R.Midlight, t.border_soft), (R.Mid, t.border),
            (R.Dark, t.border_strong), (R.Shadow, t.shadow),
            (R.Accent, t.accent)):
        pal.setColor(role, c(value))
    for role in (R.WindowText, R.Text, R.ButtonText):
        pal.setColor(G.Disabled, role, c(t.muted))
    return pal
