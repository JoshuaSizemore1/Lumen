#!/usr/bin/env python3
"""Offscreen screenshot tool for verifying PyQt6 UI against an HTML mock.

Usage:
    QT_QPA_PLATFORM=offscreen python screenshot.py app.main:build_window shots/ \
        --size 1280x800 --size 1720x1060

The target is "module.path:factory" where factory() returns the main window
(a QWidget). Repeat --size to capture multiple window sizes (verifies scaling
behavior, not just the design size). If the window contains a QStackedWidget,
every page is captured: shots/screen_00_1280x800.png, ...
"""
import argparse
import importlib
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="module.path:factory returning the main QWidget")
    parser.add_argument("outdir", help="directory for PNG output")
    parser.add_argument("--size", action="append", dest="sizes", metavar="WxH",
                        help="window size; repeatable (default: 1280x800 and 1720x1060)")
    args = parser.parse_args()
    sizes = args.sizes or ["1280x800", "1720x1060"]

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, os.getcwd())

    from PyQt6.QtWidgets import QApplication, QStackedWidget

    mod_name, _, factory_name = args.target.partition(":")
    if not factory_name:
        print("target must be 'module.path:factory'", file=sys.stderr)
        return 2
    factory = getattr(importlib.import_module(mod_name), factory_name)

    app = QApplication.instance() or QApplication(sys.argv)
    window = factory()
    window.show()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    stack = window.findChild(QStackedWidget)

    for size in sizes:
        w, _, h = size.partition("x")
        window.resize(int(w), int(h))
        app.processEvents()

        if stack is not None and stack.count() > 1:
            for i in range(stack.count()):
                stack.setCurrentIndex(i)
                app.processEvents()
                path = outdir / f"screen_{i:02d}_{size}.png"
                window.grab().save(str(path))
                print(f"saved {path}")
        else:
            path = outdir / f"window_{size}.png"
            window.grab().save(str(path))
            print(f"saved {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
