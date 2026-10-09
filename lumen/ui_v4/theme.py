"""Field Notes v1.1 design tokens — the only file in ui_v4 allowed to hold a
color literal.

`current()` is the live Theme (light or dark). `manager()` owns the user's
preference ("system" | "light" | "dark", QSettings("lumen", "ui_v4") "theme"),
follows the OS color scheme while in system mode, re-applies the app palette +
stylesheet on every switch, and emits `changed(theme)` so custom-painted
widgets can repaint.
"""
import os
import zlib
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontDatabase, QGuiApplication


@dataclass(frozen=True)
class Theme:
    mode: str                 # "light" | "dark"
    bg: str
    surface: str
    surface_warm: str
    surface_sunken: str
    fg: str
    fg2: str
    muted: str
    meta: str
    border: str
    border_strong: str
    border_soft: str
    accent: str
    accent_on: str
    accent_hover: str
    accent_active: str
    accent_soft: str
    accent_soft_fg: str
    decor: str
    success: str
    success_soft: str
    warn: str
    warn_soft: str
    danger: str
    danger_soft: str
    info: str
    info_soft: str
    tags: tuple               # Moss, Lake, Ochre, Heather, Clay
    scrim: str                # QSS rgba()
    shadow: str               # "#rrggbb" — alpha applied by the effect
    shadow_alpha: int
    toast_bg: str
    toast_fg: str
    paper: str                # HTML mail assumes a light page
    paper_fg: str
    selection: str

    @property
    def dark(self) -> bool:
        return self.mode == "dark"

    def color(self, token: str, alpha: int | None = None) -> QColor:
        """Token name (or a literal from this module) -> QColor."""
        c = QColor(getattr(self, token, token))
        if alpha is not None:
            c.setAlpha(alpha)
        return c

    def kind(self, kind: str) -> tuple[str, str]:
        """Status kind -> (ink, soft ground). neutral/accent fall back sensibly."""
        return {
            "success": (self.success, self.success_soft),
            "warn": (self.warn, self.warn_soft),
            "danger": (self.danger, self.danger_soft),
            "info": (self.info, self.info_soft),
            "accent": (self.accent_soft_fg, self.accent_soft),
        }.get(kind, (self.muted, self.surface_warm))


LIGHT = Theme(
    mode="light",
    bg="#F5F1EA", surface="#FBF9F5", surface_warm="#E8E1D4",
    surface_sunken="#E8E1D4",
    fg="#1E261F", fg2="#3A443B", muted="#5A615A", meta="#4C6B4D",
    border="#D6CDBF", border_strong="#C4B6A6", border_soft="#E6DFD3",
    accent="#4C6B4D", accent_on="#FFFFFF", accent_hover="#33493A",
    accent_active="#1E261F", accent_soft="#DCE5D5", accent_soft_fg="#2E4430",
    decor="#7D9A6D",
    success="#4C6B4D", success_soft="#DCE5D5",
    warn="#86591A", warn_soft="#F1E3C8",
    danger="#A4473A", danger_soft="#F2DCD6",
    info="#3F5F6B", info_soft="#DCE6E8",
    tags=("#3E8046", "#2F78B8", "#B7791F", "#8A5A9E", "#C0583F"),
    scrim="rgba(30,38,31,0.32)",
    shadow="#1E261F", shadow_alpha=46,
    toast_bg="#1E261F", toast_fg="#F5F1EA",
    paper="#FFFFFF", paper_fg="#1F1F1F",
    selection="#DCE5D5",
)

DARK = Theme(
    mode="dark",
    bg="#141814", surface="#1C221C", surface_warm="#252C25",
    surface_sunken="#101310",
    fg="#E8E1D4", fg2="#D3CCBF", muted="#A8A99D", meta="#9DB88C",
    border="#354035", border_strong="#4A564A", border_soft="#252C25",
    accent="#9DB88C", accent_on="#141814", accent_hover="#B2C9A3",
    accent_active="#C6D8BA", accent_soft="#263226", accent_soft_fg="#C6D8BA",
    decor="#7D9A6D",
    success="#9DB88C", success_soft="#263226",
    warn="#D9A95B", warn_soft="#33291A",
    danger="#E08A7C", danger_soft="#3A221E",
    info="#92B4BF", info_soft="#1E2B2F",
    tags=("#5FA462", "#5696CC", "#BE8633", "#A77DC2", "#CC6B53"),
    scrim="rgba(0,0,0,0.55)",
    shadow="#000000", shadow_alpha=120,
    toast_bg="#E8E1D4", toast_fg="#141814",
    paper="#FFFFFF", paper_fg="#1F1F1F",
    selection="#263226",
)

TAG_NAMES = ("Moss", "Lake", "Ochre", "Heather", "Clay")

# ---- typography ------------------------------------------------------------
# Resolved by load_fonts(); these are the fallbacks if the bundled files are
# missing or fail to register.
FONT_DISPLAY = "Georgia"
FONT_SANS = "sans-serif"
FONT_MONO = "JetBrains Mono"

H1, H2, H3 = 36, 28, 22
LEAD, BODY, SMALL, EYEBROW = 18, 16, 14, 12
UI = 14                   # default control/chrome size
EYEBROW_TRACK = 0.08      # em

# ---- spacing / radii / sizes -----------------------------------------------
SPACE = (4, 8, 12, 16, 20, 24, 32, 48, 64)
S1, S2, S3, S4, S5, S6, S8, S12, S16 = SPACE
R_TAG, R_CTRL, R_CARD, R_PILL = 4, 8, 12, 999
BTN_H, BTN_H_SM = 44, 36
ROW_H = 44
ICON, ICON_SM = 20, 16

SIDEBAR_W, RAIL_W, COLLAPSE_BELOW = 232, 64, 900
WINDOW_W, WINDOW_H = 1280, 800
MIN_W, MIN_H = 760, 560

# ---- motion ----------------------------------------------------------------
HOVER_MS, TOGGLE_MS, REVEAL_MS, EXIT_MS = 150, 220, 300, 150

MODEL_NAME = "local model"     # overwritten from config at startup

FONTS_DIR = Path(__file__).resolve().parent / "fonts"


def settings() -> QSettings:
    return QSettings("lumen", "ui_v4")


def reduced_motion() -> bool:
    env = os.environ.get("LUMEN_REDUCED_MOTION", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    return settings().value("reduced_motion", False, bool)


def ms(duration: int) -> int:
    """A motion duration, or 0 when reduced motion is on."""
    return 0 if reduced_motion() else duration


def load_fonts() -> None:
    """Register the bundled faces and resolve the family names. Safe to call
    more than once; needs a QGuiApplication."""
    global FONT_DISPLAY, FONT_SANS, FONT_MONO
    found: set[str] = set()
    if FONTS_DIR.is_dir():
        for path in sorted(FONTS_DIR.glob("*.[ot]tf")):
            fid = QFontDatabase.addApplicationFont(str(path))
            if fid >= 0:
                found.update(QFontDatabase.applicationFontFamilies(fid))
    families = set(QFontDatabase.families()) | found

    def pick(*names: str) -> str | None:
        return next((n for n in names if n in families), None)

    FONT_DISPLAY = pick("Fraunces", "Georgia", "DejaVu Serif") or "serif"
    FONT_SANS = (pick("Instrument Sans", "Inter", "Noto Sans", "Cantarell",
                      "DejaVu Sans")
                 or QFontDatabase.systemFont(
                     QFontDatabase.SystemFont.GeneralFont).family())
    FONT_MONO = (pick("JetBrains Mono", "DejaVu Sans Mono")
                 or QFontDatabase.systemFont(
                     QFontDatabase.SystemFont.FixedFont).family())


def tag_color(key, theme: "Theme | None" = None) -> str:
    """Stable chip dot color. An int picks a slot; a string hashes to one
    (crc32, not hash(): hash() is salted per process)."""
    t = theme or current()
    if isinstance(key, int):
        return t.tags[key % len(t.tags)]
    return t.tags[zlib.crc32(str(key).casefold().encode()) % len(t.tags)]


def tag_slot(name: str) -> int:
    return zlib.crc32(str(name).casefold().encode()) % len(TAG_NAMES)


# ---- mode resolution + live switching --------------------------------------
PREFS = ("system", "light", "dark")


def preference() -> str:
    p = settings().value("theme", "system", str)
    return p if p in PREFS else "system"


def system_mode() -> str:
    app = QGuiApplication.instance()
    if app is None:
        return "light"
    return ("dark" if app.styleHints().colorScheme() == Qt.ColorScheme.Dark
            else "light")


def resolve_mode(pref: str | None = None) -> str:
    pref = pref or preference()
    return system_mode() if pref == "system" else pref


class ThemeManager(QObject):
    changed = pyqtSignal(object)     # the new Theme

    def __init__(self):
        super().__init__()
        self._pref = preference()
        self._theme = DARK if resolve_mode(self._pref) == "dark" else LIGHT
        app = QGuiApplication.instance()
        if app is not None:
            app.styleHints().colorSchemeChanged.connect(self._on_system_changed)

    @property
    def theme(self) -> Theme:
        return self._theme

    @property
    def pref(self) -> str:
        return self._pref

    def set_pref(self, pref: str) -> None:
        if pref not in PREFS:
            return
        self._pref = pref
        settings().setValue("theme", pref)
        self._set_mode(resolve_mode(pref), force=True)

    def cycle(self) -> str:
        """The sidebar toggle: system -> the opposite of what's showing ->
        its opposite -> ... Once explicit it stays explicit (spec)."""
        nxt = "light" if self._theme.dark else "dark"
        self.set_pref(nxt)
        return nxt

    def _on_system_changed(self, *_):
        if self._pref == "system":
            self._set_mode(system_mode())

    def _set_mode(self, mode: str, force: bool = False) -> None:
        theme = DARK if mode == "dark" else LIGHT
        if theme is self._theme and not force:
            return
        self._theme = theme
        self.apply()
        self.changed.emit(theme)

    def apply(self, app=None) -> None:
        """Push palette + stylesheet onto the application."""
        from .styles import app_palette, build_qss
        app = app or QGuiApplication.instance()
        if app is None:
            return
        app.setPalette(app_palette(self._theme))
        if hasattr(app, "setStyleSheet"):
            app.setStyleSheet(build_qss(self._theme))


_manager: ThemeManager | None = None


def manager() -> ThemeManager:
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def current() -> Theme:
    if _manager is None:
        if QGuiApplication.instance() is None:
            return LIGHT
        return manager().theme
    return _manager.theme
