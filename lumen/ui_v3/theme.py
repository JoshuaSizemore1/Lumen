"""Design tokens extracted from the Relay mockup (Relay.dc.html).

Every value here is a literal hex / px lifted from the mockup's inline CSS —
nothing is eyeballed. Accent is themeable: the mockup ships five options and
derives every tinted fill from `--ac`, so `set_accent()` does the same.

Font substitution: the mockup asks for 'Hanken Grotesk' + 'Space Mono', neither
of which is installed on this machine. IBM Plex Sans / IBM Plex Mono stand in —
same humanist-grotesque and mono-with-personality roles, and they pair.
"""
import zlib

# ---- Contrast calibration ------------------------------------------------
# The literals below are the mockup's own hexes, and at 1.0 these knobs pass
# them through untouched — which is the intended state. They exist because the
# shell once looked washed out beside the mock; the window was being screenshot
# unfocused, and Hyprland renders inactive windows at 0.75 opacity over a
# blurred wallpaper. Nothing was wrong with the tokens. Raise a knob only
# against a *focused* window that still reads too light.
DEPTH_SURFACE = 1.0     # backgrounds
DEPTH_BORDER = 1.0      # rules and outlines
DEPTH_TEXT = 0.86       # the whole ink ramp; Josh asked for darker type 07-20


def _deepen(hex_color: str, k: float) -> str:
    """Scale every channel by k, pulling blue down slightly harder.

    A flat multiply keeps the channel ratios but shrinks the absolute r-b
    spread, so the cream greys off as it darkens. Taking a little extra off
    blue holds the warmth — which is how the mock's own ramp behaves: its
    darker surfaces are *warmer*, not just dimmer."""
    if k == 1.0:
        return hex_color      # literal mock; the warm bias must not apply
    bias = (1.0, 0.997, 0.978)
    r, g, b = (min(255, round(int(hex_color[i:i + 2], 16) * k * f))
               for i, f in zip((1, 3, 5), bias))
    return f"#{r:02x}{g:02x}{b:02x}"


# ---- Surfaces ------------------------------------------------------------
_S = DEPTH_SURFACE
BG_PAGE = _deepen("#efe7d6", _S)        # backdrop behind the app frame
BG_SHELL = _deepen("#f7f1e4", _S)       # app frame; also todo rail + ask-bar
BG_SIDEBAR = _deepen("#f0e9d8", _S)     # left nav column; also folder cards
BG_MAIN = _deepen("#faf5ea", _S)        # main content canvas; also dialogs
BG_FIELD = _deepen("#f2ebdb", _S)       # inputs, chat bubbles, answer panel
BG_CARD = _deepen("#f7f1e4", _S)        # file cards (non-folder)
BG_OUT_MONTH = _deepen("#f2ecdd", _S)   # month cells outside the current month
BG_INPUT = _deepen("#faf5ea", _S)       # inputs sitting *on* a panel (books)
BG_TOAST = "#2a2620"
BG_SCRIM = "rgba(50,42,26,0.38)"        # modal backdrop
BG_SCRIM_LAUNCH = "rgba(45,40,26,0.40)"

# ---- Borders -------------------------------------------------------------
_B = DEPTH_BORDER
BORDER_STRONG = _deepen("#d7cdb4", _B)  # window frame, ghost buttons, rules
BORDER_MED = _deepen("#e4dcc7", _B)     # section dividers, sidebar edge
BORDER_FAINT = _deepen("#ede5d2", _B)   # row separators, calendar gridlines
BORDER_FIELD = _deepen("#e0d7c0", _B)   # input/panel borders
BORDER_INPUT = _deepen("#ddd3bb", _B)   # inputs on a panel
BORDER_TRACK = _deepen("#e8e0cb", _B)   # progress-bar track
BORDER_DUE = _deepen("#e2d4ab", _B)     # due-date chip
BORDER_DANGER = _deepen("#d9b8ab", _B)  # Delete button
BORDER_OK = _deepen("#cdddbc", _B)      # todo-added confirmation panel

# ---- Text ----------------------------------------------------------------
_T = DEPTH_TEXT
TEXT_PRIMARY = _deepen("#2a2620", _T)       # headings, unread mail, todo text
TEXT_BODY = _deepen("#3a352c", _T)          # mail body, file text, settings
TEXT_SECONDARY = _deepen("#6b6353", _T)     # inactive nav, ghost labels, notes
TEXT_MUTED = _deepen("#9a917d", _T)         # previews, meta
TEXT_FAINT = _deepen("#a1957c", _T)         # mono eyebrows, counts
TEXT_FAINTER = _deepen("#bcae90", _T)       # hour labels, input hints
TEXT_GHOST = _deepen("#c3b699", _T)         # ✕ affordances, read-mail dot
TEXT_OUT_MONTH = _deepen("#b8ad92", _T)     # out-of-month chips, "all" tag dot
TEXT_READ = _deepen("#8a8371", _T)          # sender on an already-read message

# ---- Accent (user-selectable; picker lives in Settings) ------------------
ACCENT_OPTIONS = {
    "terracotta": "#a24e34",
    "blue": "#2f5aac",
    "green": "#4f6f3a",
    "purple": "#6a4bb0",
    "slate": "#3d4f66",
}
DEFAULT_ACCENT = "terracotta"   # the mockup's default
ACCENT_ON = "#ffffff"           # text on accent-filled buttons
ACCENT_LINK_HOVER = "#7f3a26"


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


# The mock tints at 10% / 18% (--ac-soft / --ac-mid) and 11% for event blocks.
# One factor scales all three together; 1.0 is the mock's own ratios.
TINT_BOOST = 1.0
TINT_SOFT = 0.102 * TINT_BOOST
TINT_MID = 0.180 * TINT_BOOST
TINT_EVENT = 0.110 * TINT_BOOST


def set_accent(hex_color: str) -> None:
    """Swap the accent; every derived fill follows. Rebuild the QSS after."""
    global ACCENT, ACCENT_SOFT, ACCENT_MID, ACCENT_SOFT_QSS, ACCENT_MID_QSS
    ACCENT = hex_color
    # CSS-side alpha is written last (#rrggbbaa) — that is what the painters take.
    ACCENT_SOFT = ACCENT + f"{round(TINT_SOFT * 255):02x}"
    ACCENT_MID = ACCENT + f"{round(TINT_MID * 255):02x}"
    # QSS reads #aarrggbb, so tinted fills go through rgba() there instead.
    ACCENT_SOFT_QSS = _rgba(ACCENT, round(TINT_SOFT, 3))
    ACCENT_MID_QSS = _rgba(ACCENT, round(TINT_MID, 3))


set_accent(ACCENT_OPTIONS[DEFAULT_ACCENT])

# ---- Semantic / status ---------------------------------------------------
OK = "#4f6f3a"
WARN = "#9a7420"
INFO = "#2a7ba0"
STAR = "#9a7420"

# Books "suggested next" panel runs its own purple scheme, independent of accent.
BOOK_ACCENT = "#6a4bb0"
BOOK_TITLE = "#4a3a6e"
BOOK_LABEL = "#9985b7"
BOOK_WHY = "#7a6b9e"
BOOK_FOOT = "#b3a5cc"
BOOK_BG = "#f4eef9"
BOOK_BORDER = "#ddccec"
BOOK_ROW_BORDER = "#e6dbf2"

# Calendar categories (mockup CAT map). Live Google events carry their own
# color and fall through these.
CAL_COLORS = {
    "work": "#2f5aac",
    "personal": "#6a4bb0",
    "health": "#4f6f3a",
    "social": "#b07615",
}

TAG_COLORS = {
    "work": "#2f5aac",
    "personal": "#6a4bb0",
    "home": "#4f6f3a",
    "admin": "#9a7420",
    "tinker": "#2a7ba0",
}

# Mail label pills: stable color per label name. crc32, not hash() — hash() is
# salted per process and would reshuffle every launch.
LABEL_PALETTE = ("#2f5aac", "#6a4bb0", "#9a7420", "#4f6f3a",
                 "#2a7ba0", "#a24e34", "#b07615", "#3d4f66")


def label_color(name: str) -> str:
    return LABEL_PALETTE[zlib.crc32(name.casefold().encode()) % len(LABEL_PALETTE)]


def tag_color(tag: str) -> str:
    return TAG_COLORS.get(tag, ACCENT)


def cal_color(ev: dict) -> str:
    """Event -> paint color. Sample events name a category; live Google events
    carry a real hex that has no category, so the hex wins."""
    name = (ev.get("cal") or "").lower()
    if name in CAL_COLORS:
        return CAL_COLORS[name]
    return ev.get("color") or TEXT_MUTED


# ---- Typography ----------------------------------------------------------
FONT_SANS = "IBM Plex Sans"   # substitutes 'Hanken Grotesk'
FONT_MONO = "IBM Plex Mono"   # substitutes 'Space Mono'

# ---- Font scale (Settings > interface > text size) ------------------------
# One user-facing percentage drives every type size *and* every fixed pixel
# dimension that exists to hold type — panel widths, control heights, chip
# padding, row heights. Scaling the fonts alone would just clip them out of
# their boxes at 150%, so `sc()` is applied wherever a literal px was chosen to
# fit text. Sizes below are the design's own values, i.e. 100%.
FONT_SCALE = 1.0
FONT_SCALE_MIN, FONT_SCALE_MAX = 50, 150


def sc(px: float) -> int:
    """Scale a fixed pixel dimension by the current text-size setting."""
    return max(1, round(px * FONT_SCALE))


# Rebuilt by set_font_scale(); the values here are the 100% baseline.
_LAYOUT = {
    "WINDOW_W": 1280,          # the mockup's app frame
    "WINDOW_H": 788,
    "SIDEBAR_W": 220,
    "MAIL_LIST_W": 346,
    "TODO_RAIL_W": 264,
    "CHAT_LIST_W": 270,
    "AGENDA_W": 284,
    "BOOK_REC_W": 366,
    "SETTINGS_MAX_W": 800,
    "LAUNCHER_W": 560,
    "CONFIRM_W": 490,
    "DRAFT_W": 560,
    "EVENT_W": 470,
    "GRID_HOUR_H": 46,         # week/day time grid
    "MONTH_CELL_MIN_H": 108,
    "DASH_HOUR_H": 50,         # the denser Today mini-schedule
}

# Not scaled: the window must stay shrinkable on a small display, and the hour
# bounds are clock values rather than sizes.
MIN_W, MIN_H = 1120, 700
GRID_START_H = 7
GRID_END_H = 21
DASH_START_H = 8
DASH_END_H = 20


def set_font_scale(pct: float) -> None:
    """Set the UI text scale, as a percentage. Rebuild the window afterwards —
    fonts and layout metrics are both read at widget construction."""
    global FONT_SCALE
    FONT_SCALE = max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, pct)) / 100
    globals().update({name: sc(base) for name, base in _LAYOUT.items()})


set_font_scale(100)

MODEL_NAME = "qwen3:4b-instruct"   # overridden from live config at startup
