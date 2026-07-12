"""Design tokens extracted from lumenFullHTMLUI.html / design/tokens.md.

All values are literal hex + px from the mockup CSS. Accent is themeable:
everything accent-tinted derives from ACCENT at import time.
"""

# ---- Surfaces ----------------------------------------------------------
BG_APP = "#0d0e14"        # backdrop behind the window (mock page bg)
BG_WINDOW = "#1a1b26"     # main window body
BG_CHROME = "#16161e"     # title bar + tab bar
BG_OVERLAY = "#101018"    # launcher canvas
BG_PANEL = "#171821"      # cards, launcher palette, add-forms
BG_PANEL_ALT = "#14151d"  # inset headers/footers, calendar grid bg
BG_FIELD = "#0f1017"      # text inputs inside panels
BG_INSET = "#12131b"      # confirmation "what will happen" box
BG_WAYBAR = "#0c0d13"     # faux status bar strip on launcher
BG_DIALOG = "#1c1d28"     # confirm dialog / toast body

# ---- Borders -----------------------------------------------------------
BORDER_STRONG = "#2a2f45"
BORDER_MED = "#262838"
BORDER_SOFT = "#20222e"
BORDER_FAINT = "#1a1c26"
BORDER_FAINTEST = "#1e202c"
BORDER_WAYBAR = "#1c1d28"

# ---- Text ---------------------------------------------------------------
TEXT_PRIMARY = "#c0caf5"
TEXT_SECONDARY = "#a9b1d6"
TEXT_MUTED = "#787c99"
TEXT_BODY_MUTED = "#8a90b3"   # mail preview-on-dashboard, dialog intro, notes
TEXT_DIM = "#565f89"
TEXT_FAINT = "#3b4261"
TEXT_GHOST = "#2a2f45"

# ---- Accent (user-selectable; picker lives in Settings) ------------------
ACCENT_OPTIONS = {
    "blue": "#7aa2f7",
    "green": "#9ece6a",
    "amber": "#e0af68",
    "purple": "#bb9af7",
    "pink": "#f7768e",
}
DEFAULT_ACCENT = "green"  # user pick 2026-07-10

ACCENT_ON = "#0d0e14"     # text on accent-filled buttons


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def set_accent(hex_color: str):
    """Swap the accent; derived fills follow. QSS must be rebuilt after."""
    global ACCENT, ACCENT_SOFT, ACCENT_MID, ACCENT_SOFT_QSS, ACCENT_MID_QSS
    ACCENT = hex_color
    ACCENT_SOFT = ACCENT + "1f"   # ~12% fill (CSS-style alpha-last, for painting)
    ACCENT_MID = ACCENT + "33"    # ~20% fill
    # QSS cannot take #rrggbbaa (it reads alpha-first); use rgba() strings there.
    ACCENT_SOFT_QSS = _rgba(ACCENT, 0.12)
    ACCENT_MID_QSS = _rgba(ACCENT, 0.20)


set_accent(ACCENT_OPTIONS[DEFAULT_ACCENT])

# ---- Semantic / status ---------------------------------------------------
OK = "#9ece6a"
WARN = "#e0af68"
NOW = "#f7768e"
INFO = "#7dcfff"
BOOK = "#bb9af7"              # recommendations accent
BOOK_TITLE = "#c9b8f0"
BOOK_DIM = "#8f83ac"          # rec rationale text
BOOK_LABEL = "#6b5d8f"        # rec authors / eyebrow
BOOK_BORDER = "#3b3155"       # dashed panel border
BOOK_ROW_BORDER = "#241d33"
BOOK_FOOT = "#4a4066"
BOOK_GRAD_TOP = "#1a1626"
BOOK_GRAD_BOT = "#16141f"
TAG_NEW = "#ff9e64"

TAG_COLORS = {
    "work": "#7aa2f7",
    "personal": "#bb9af7",
    "home": "#9ece6a",
    "admin": "#e0af68",
    "tinker": "#7dcfff",
    "new": "#ff9e64",
}

# Calendar category colors
CAL_COLORS = {
    "work": "#7aa2f7",
    "personal": "#bb9af7",
    "health": "#9ece6a",
    "social": "#e0af68",
}

# ---- Typography ----------------------------------------------------------
FONT_MONO = "JetBrains Mono"
FONT_SANS = "IBM Plex Sans"

# ---- Layout ---------------------------------------------------------------
WINDOW_W = 1320
TITLEBAR_H = 38
TABBAR_H = 38
CONTENT_H = 722
WINDOW_H = TITLEBAR_H + TABBAR_H + CONTENT_H  # 798
WAYBAR_H = 24

# Density (cozy preset from the mock)
ROW_PY = 7
GAP = 11
PAD = 15

MODEL_NAME = "qwen3:4b-instruct"  # default label; the app overrides with the live config model
DATE_LABEL = "Thu · Jul 4"
