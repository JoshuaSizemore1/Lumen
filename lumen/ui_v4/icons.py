"""One outlined icon family: 24px grid, 1.5 stroke, round caps and joins.

Each entry is the inner SVG markup; `currentColor` is substituted with the
requested color at render time, so any icon comes out in any token.

    icon("mail")                    # QIcon in theme fg2, 20px
    icon("trash", "danger", 16)     # token names or literal colors
    pixmap("check", "accent_on", 14)
    icon_path("chevron-down", "muted")   # PNG on disk, for QSS image: url()
"""
import tempfile
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from . import theme as T

ICONS: dict[str, str] = {
    "today": '<path d="M12 3v3M5.64 7.64l1.42 1.42M18.36 7.64l-1.42 1.42M3 17h18'
             'M7 17a5 5 0 0 1 10 0M8 21h8"/>',
    "leaf": '<path d="M4.5 19.5C5 11 10 5.5 20 4c-1.2 10-6.5 15.3-15.5 15.5z"/>'
            '<path d="M4.5 19.5 13 11"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2'
           'M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M2.5 12h2M19.5 12h2'
           'M6.7 17.3l-1.4 1.4M18.7 5.3l-1.4 1.4"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 0 0 10.5 10.5z"/>',
    "inbox": '<path d="M3 13l2.6-7.2A1.5 1.5 0 0 1 7 5h10a1.5 1.5 0 0 1 1.4.8'
             'L21 13v5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18z"/>'
             '<path d="M3 13h5l1.5 2.5h5L16 13h5"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/>'
            '<path d="m3.5 6.5 8.5 6.5 8.5-6.5"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/>'
                '<path d="M8 3v4M16 3v4M3.5 10h17"/>',
    "todos": '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/>'
             '<path d="m8 12 3 3 5-6"/>',
    "books": '<path d="M12 7c-1.5-1.6-4-2.5-8.5-2.5v14c4.5 0 7 .9 8.5 2.5 '
             '1.5-1.6 4-2.5 8.5-2.5v-14C16 4.5 13.5 5.4 12 7z"/>'
             '<path d="M12 7v14"/>',
    "book": '<path d="M5 19.5V5A1.5 1.5 0 0 1 6.5 3.5H19v14H7a2 2 0 0 0-2 2z'
            'm0 0a2 2 0 0 0 2 2h12"/><path d="M9 7.5h6"/>',
    "files": '<path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2.5h8a1.5 1.5 0 0 1 '
             '1.5 1.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    "file": '<path d="M14 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10'
            'a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3.5V8h4.5"/>',
    "canvas": '<path d="M2.5 9 12 4.5 21.5 9 12 13.5z"/>'
              '<path d="M6.5 11v4.5c0 1.5 2.5 3 5.5 3s5.5-1.5 5.5-3V11"/>'
              '<path d="M21.5 9v5"/>',
    "ask": '<path d="M20.5 12a8.5 8.5 0 0 1-12.3 7.6L3.5 20.5l1.1-4.4'
           'A8.5 8.5 0 1 1 20.5 12z"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/>'
                '<circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/>'
                '<circle cx="17" cy="18" r="2"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "alert": '<path d="M10.3 4.2 2.9 17.5a2 2 0 0 0 1.7 3h14.8a2 2 0 0 0 1.7-3'
             'L13.7 4.2a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 17h.01"/>',
    "alert-circle": '<circle cx="12" cy="12" r="8.5"/>'
                    '<path d="M12 8v4.5M12 16h.01"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
    "arrow-left": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "arrow-up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m6 15 6-6 6 6"/>',
    "chevron-right": '<path d="m9 6 6 6-6 6"/>',
    "chevron-left": '<path d="m15 6-6 6 6 6"/>',
    "refresh": '<path d="M20 11.5A8 8 0 0 0 6.3 6.3L4 8.5"/>'
               '<path d="M4 4v4.5h4.5"/>'
               '<path d="M4 12.5a8 8 0 0 0 13.7 5.2l2.3-2.2"/>'
               '<path d="M20 20v-4.5h-4.5"/>',
    "reply": '<path d="M9.5 8 4.5 12.5l5 4.5"/>'
             '<path d="M4.5 12.5H14a5.5 5.5 0 0 1 5.5 5.5v1"/>',
    "forward": '<path d="m14.5 8 5 4.5-5 4.5"/>'
               '<path d="M19.5 12.5H10a5.5 5.5 0 0 0-5.5 5.5v1"/>',
    "archive": '<rect x="3" y="4" width="18" height="4.5" rx="1"/>'
               '<path d="M4.5 8.5V18A1.5 1.5 0 0 0 6 19.5h12a1.5 1.5 0 0 0 '
               '1.5-1.5V8.5M10 12.5h4"/>',
    "trash": '<path d="M4 7h16M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2'
             'M6 7l1 12a1.5 1.5 0 0 0 1.5 1.5h7A1.5 1.5 0 0 0 17 19l1-12'
             'M10 11v5.5M14 11v5.5"/>',
    "edit": '<path d="M16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1 1-4z"/>'
            '<path d="m14.5 6.5 3 3"/>',
    "send": '<path d="M20.5 3.5 10 14"/>'
            '<path d="m20.5 3.5-6.5 17-4-6.5-6.5-4z"/>',
    "star": '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 '
            '1-5.8-4.3-4.1 5.9-.9z"/>',
    # Rating glyph only: the same outline, filled.
    "star-fill": '<path fill="currentColor" d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 '
                 '1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    "external": '<path d="M14 4.5h5.5V10M19.5 4.5 11 13M17 13.5v5a1.5 1.5 0 0 1'
                '-1.5 1.5h-10A1.5 1.5 0 0 1 4 18.5v-10A1.5 1.5 0 0 1 5.5 7h5"/>',
    "tag": '<path d="M3.5 12.3V4.5a1 1 0 0 1 1-1h7.8l8.2 8.2a1.5 1.5 0 0 1 0 '
           '2.1l-6.7 6.7a1.5 1.5 0 0 1-2.1 0z"/><circle cx="8" cy="8" r="1.3"/>',
    "filter": '<path d="M3.5 5h17l-6.5 7.5V19l-4 1.5v-8z"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    "download": '<path d="M12 4v11M7 10.5l5 5 5-5M4.5 20h15"/>',
    "upload": '<path d="M12 16V5M7 9.5l5-5 5 5M4.5 20h15"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 '
           '2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M10.6 5.6A9.8 9.8 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5'
               'a17 17 0 0 1-2.6 3.4M6.6 6.6C4 8.3 2.5 12 2.5 12s3.5 6.5 9.5 '
               '6.5a9.4 9.4 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2M3.5 3.5l17 17"/>',
    "command": '<path d="M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3'
               'h12a3 3 0 1 0-3-3z"/>',
    "more-horizontal": '<circle cx="5.5" cy="12" r="1"/><circle cx="12" cy="12" '
                       'r="1"/><circle cx="18.5" cy="12" r="1"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/>'
            '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/>'
            '<path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    "logout": '<path d="M9.5 20.5H6A1.5 1.5 0 0 1 4.5 19V5A1.5 1.5 0 0 1 6 3.5'
              'h3.5M15.5 16.5 20 12l-4.5-4.5M20 12H9.5"/>',
    "undo": '<path d="M9 14 4.5 9.5 9 5"/><path d="M4.5 9.5H15a5 5 0 0 1 0 10h-3"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="1.5"/>'
            '<path d="M15.5 8.5v-3A1.5 1.5 0 0 0 14 4H5.5A1.5 1.5 0 0 0 4 5.5V14'
            'a1.5 1.5 0 0 0 1.5 1.5h3"/>',
    "save": '<path d="M16 3.5H6A1.5 1.5 0 0 0 4.5 5v14A1.5 1.5 0 0 0 6 20.5h12'
            'a1.5 1.5 0 0 0 1.5-1.5V7z"/><path d="M8 3.5v4h7M8 20.5V14h8v6.5"/>',
    "bell": '<path d="M6 9.5a6 6 0 0 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15.5 6 9.5z"/>'
            '<path d="M10 20a2 2 0 0 0 4 0"/>',
    "map-pin": '<path d="M12 21s-6.5-5.5-6.5-11a6.5 6.5 0 0 1 13 0C18.5 15.5 12 '
               '21 12 21z"/><circle cx="12" cy="10" r="2.3"/>',
    "paperclip": '<path d="m20 11.5-8 8a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 '
                 '4.7L9.7 17.2a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8"/>',
    "power": '<path d="M12 3.5v8M7 6.5a7 7 0 1 0 10 0"/>',
    "sidebar": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/>'
               '<path d="M9.5 4.5v15"/>',
    "dot": '<circle cx="12" cy="12" r="3.5" fill="currentColor"/>',
    "circle": '<circle cx="12" cy="12" r="8.5"/>',
    # Theme toggle's "follow the system" state: half sun, half moon.
    "contrast": '<circle cx="12" cy="12" r="8.5"/>'
                '<path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor"/>',
}

# Aliases so screens can ask by role rather than by shape.
ALIASES = {
    "check-square": "todos", "folder": "files", "chat": "ask",
    "graduation": "canvas", "sliders": "settings", "close": "x",
    "warning": "alert", "pen": "edit", "delete": "trash",
    "more": "more-horizontal", "back": "arrow-left", "location": "map-pin",
    "attachment": "paperclip", "time": "clock",
}

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="{w}" stroke-linecap="round" '
        'stroke-linejoin="round">{body}</svg>')

_cache: dict[tuple, QPixmap] = {}
_img_dir: Path | None = None


def names() -> list[str]:
    return sorted(ICONS)


def _resolve_color(color: str | None) -> str:
    t = T.current()
    if color is None:
        return t.fg2
    return getattr(t, color, color) if not color.startswith(("#", "rgb")) else color


def _body(name: str) -> str:
    return ICONS.get(ALIASES.get(name, name), ICONS["circle"])


def _render(name: str, color: str, px: int, stroke: float) -> QImage:
    svg = _SVG.format(c=color, w=stroke, body=_body(name)).replace(
        "currentColor", color)
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, px, px))
    p.end()
    return img


def _dpr() -> float:
    app = QGuiApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    return screen.devicePixelRatio() if screen is not None else 1.0


def pixmap(name: str, color: str | None = None, size: int = T.ICON,
           stroke: float = 1.5) -> QPixmap:
    c = _resolve_color(color)
    dpr = _dpr()
    key = (name, c, size, stroke, dpr)
    pm = _cache.get(key)
    if pm is None:
        pm = QPixmap.fromImage(_render(name, c, max(1, round(size * dpr)), stroke))
        pm.setDevicePixelRatio(dpr)
        _cache[key] = pm
    return pm


def icon(name: str, color: str | None = None, size: int = T.ICON,
         disabled_color: str | None = "muted") -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Active)
    if disabled_color:
        ic.addPixmap(pixmap(name, disabled_color, size), QIcon.Mode.Disabled)
    return ic


def icon_path(name: str, color: str | None = None, size: int = 16) -> str:
    """A PNG on disk (2x), for QSS `image: url(...)` slots."""
    global _img_dir
    if _img_dir is None:
        _img_dir = Path(tempfile.gettempdir()) / "lumen-ui-v4-icons"
        _img_dir.mkdir(parents=True, exist_ok=True)
    c = _resolve_color(color)
    path = _img_dir / f"{name}-{c.lstrip('#')}-{size}.png"
    if not path.exists():
        _render(name, c, size * 2, 1.5 * 1.0).save(str(path))
    return path.as_posix()


def clear_cache() -> None:
    _cache.clear()
