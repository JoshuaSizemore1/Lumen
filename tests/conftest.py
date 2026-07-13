import os

# Force, don't setdefault: a desktop session exports QT_QPA_PLATFORM
# ("wayland;xcb"), and tests that show() a widget then hit the real
# compositor — flaky paint-at-exit segfaults and windows popping over
# the user's desktop. Tests always render offscreen.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
