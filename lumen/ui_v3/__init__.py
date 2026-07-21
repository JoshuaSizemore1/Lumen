"""Relay-styled Lumen UI, converted from Relay.dc.html.

The primary Lumen UI as of 2026-07-20 — `lumen`, `lumen-ui`, and
`python -m lumen.ui_v3` all launch it. The previous tabbed shell is archived
as a fallback under `lumen-ui-v2`. Shares ui_v2's AppState (the daemon seam),
mail_html, daemon_client, single_instance, and tray — see state.py for the
migration note.
"""

# A distinct socket from ui_v2's default ("lumen-ui"), so the archived v2 shell
# and this one can run at once without stealing each other's hotkey. Lives here,
# not in app.py, so the `lumen` launcher's hotkey fast-path can read it without
# importing the whole UI stack.
SOCKET_NAME = "lumen-ui-v3"
