"""Tray icon + menu (SNI — shows up in a waybar tray module)."""
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import theme as T


def _icon() -> QIcon:
    pm = QPixmap(22, 22)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(T.ACCENT))
    p.setPen(QColor(T.ACCENT))
    p.drawEllipse(4, 4, 14, 14)
    p.end()
    return QIcon(pm)


def make_tray(app: QApplication, on_show, on_toggle_launcher, on_sleep) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(_icon(), app)
    menu = QMenu()
    menu.addAction("Show Lumen", on_show)
    menu.addAction("Toggle launcher", on_toggle_launcher)
    menu.addSeparator()
    menu.addAction("Sleep model now", on_sleep)
    menu.addSeparator()
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip("Lumen — daily assistant")
    tray.show()
    tray._menu = menu  # keep a Python ref: setContextMenu doesn't take ownership
    return tray
