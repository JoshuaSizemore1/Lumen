"""Relay-styled Lumen UI, converted from Relay.dc.html.

The primary Lumen UI from 2026-07-20 until 2026-10-10, when ui_v4 took over
`lumen` and `lumen-ui`. Still runnable as `lumen-ui-v3` or
`python -m lumen.ui_v3`. The older tabbed shell is archived as `lumen-ui-v2`. Shares ui_v2's AppState (the daemon seam),
mail_html, daemon_client, single_instance, and tray — see state.py for the
migration note.
"""

# A distinct socket from ui_v2's default ("lumen-ui"), so the archived v2 shell
# and this one can run at once without stealing each other's hotkey. Lives here,
# not in app.py, so the `lumen` launcher's hotkey fast-path can read it without
# importing the whole UI stack.
SOCKET_NAME = "lumen-ui-v3"
