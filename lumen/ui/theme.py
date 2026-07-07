"""Design tokens from .claude/lumenFrontEndUIReference/design/tokens.md, as code.
Accent is themeable: build_qss(accent) threads one color through everything."""

BG_APP = "#0d0e14"; BG_WINDOW = "#1a1b26"; BG_CHROME = "#16161e"
BG_OVERLAY = "#101018"; BG_PANEL = "#171821"; BG_PANEL_ALT = "#14151d"
BG_FIELD = "#0f1017"; BG_INSET = "#12131b"; BG_WAYBAR = "#0c0d13"
BORDER_STRONG = "#2a2f45"; BORDER_MED = "#262838"; BORDER_SOFT = "#20222e"; BORDER_FAINT = "#1a1c26"
TEXT_PRIMARY = "#c0caf5"; TEXT_SECONDARY = "#a9b1d6"; TEXT_MUTED = "#787c99"
TEXT_DIM = "#565f89"; TEXT_FAINT = "#3b4261"
ACCENT = "#7aa2f7"
ACCENT_SOFT = "rgba(122,162,247,0.12)"; ACCENT_MID = "rgba(122,162,247,0.20)"
OK = "#9ece6a"; WARN = "#e0af68"; NOW = "#f7768e"; INFO = "#7dcfff"
BOOK = "#bb9af7"; BOOK_DIM = "#8f83ac"; TAG_NEW = "#ff9e64"
FONT_MONO = "JetBrains Mono"; FONT_SANS = "IBM Plex Sans"
TAG_COLORS = {"work": ACCENT, "personal": BOOK, "home": OK,
              "admin": WARN, "tinker": INFO, "new": TAG_NEW}


def _soft(accent: str) -> str:
    r, g, b = (int(accent[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},0.12)"


def build_qss(accent: str = ACCENT) -> str:
    ac_soft = _soft(accent)
    return f"""
* {{ font-family: '{FONT_MONO}'; font-size: 13px; color: {TEXT_PRIMARY}; }}
QWidget[role="window"] {{ background: {BG_WINDOW}; }}
QWidget[role="chrome"] {{ background: {BG_CHROME}; }}
QWidget[role="waybar"] {{ background: {BG_WAYBAR}; }}
QWidget[role="overlay"] {{ background: {BG_OVERLAY}; }}

QLabel[role="eyebrow"] {{ font-size: 10px; color: {TEXT_FAINT}; }}
QLabel[role="accent-eyebrow"] {{ font-size: 10px; color: {accent}; }}
QLabel[role="h2"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="sub"] {{ font-size: 11px; color: {TEXT_DIM}; }}
QLabel[role="secondary"] {{ color: {TEXT_SECONDARY}; }}
QLabel[role="muted"] {{ font-size: 12px; color: {TEXT_MUTED}; }}
QLabel[role="dim"] {{ font-size: 11px; color: {TEXT_DIM}; }}
QLabel[role="faint"] {{ font-size: 10px; color: {TEXT_FAINT}; }}
QLabel[role="sans"] {{ font-family: '{FONT_SANS}'; }}
QLabel[role="chip"] {{ font-size: 10px; border: 1px solid {BORDER_STRONG};
                       border-radius: 3px; padding: 1px 5px; }}
QLabel[role="kbd"] {{ font-size: 10px; color: {TEXT_FAINT}; border: 1px solid {BORDER_STRONG};
                      border-radius: 3px; padding: 1px 5px; }}
QLabel[role="error"] {{ color: {NOW}; font-size: 12px; }}
QLabel[role="status"] {{ color: {WARN}; font-size: 11px; }}

QFrame[role="panel"] {{ background: {BG_PANEL}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; }}
QFrame[role="panel-alt"] {{ background: {BG_PANEL_ALT}; border: 1px solid {BORDER_SOFT}; border-radius: 8px; }}
QFrame[role="inset"] {{ background: {BG_INSET}; border: 1px solid {BORDER_MED}; border-radius: 7px; }}
QFrame[role="hline"] {{ background: {BORDER_FAINT}; border: none; max-height: 1px; }}

QPushButton {{ font-size: 11.5px; }}
QPushButton[kind="primary"] {{ background: {accent}; color: {BG_APP}; font-weight: 600;
    border: 1px solid {accent}; border-radius: 6px; padding: 6px 14px; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid {BORDER_STRONG};
    color: {TEXT_SECONDARY}; border-radius: 6px; padding: 6px 12px; }}
QPushButton[kind="link"] {{ background: transparent; border: 1px solid {BORDER_STRONG};
    color: {accent}; font-size: 11px; border-radius: 6px; padding: 7px; }}
QPushButton[kind="soft"] {{ background: {ac_soft}; border: 1px solid {accent};
    color: {TEXT_PRIMARY}; font-size: 11px; border-radius: 5px; padding: 5px 12px; }}
QPushButton[kind="tab"] {{ background: transparent; border: none; color: {TEXT_DIM};
    font-size: 12px; padding: 10px 14px; border-bottom: 2px solid transparent; }}
QPushButton[kind="tab"][active="true"] {{ color: {TEXT_PRIMARY}; border-bottom: 2px solid {accent}; }}
QPushButton[kind="tab"]:hover {{ color: {TEXT_SECONDARY}; }}

QLineEdit {{ background: transparent; border: none; color: {TEXT_PRIMARY}; font-size: 16px; }}
QLineEdit[kind="field"] {{ background: {BG_FIELD}; border: 1px solid {BORDER_STRONG};
    border-radius: 5px; padding: 7px 9px; font-size: 12.5px; }}
QTextEdit {{ background: transparent; border: none; font-family: '{FONT_SANS}'; font-size: 14px; }}
QCheckBox {{ spacing: 10px; font-size: 13px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {TEXT_FAINT}; border-radius: 3px; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
"""
