"""Field Notes redesign of the Lumen UI (v4).

Launch with `lumen-ui-v4` or `python -m lumen.ui_v4`. ui_v3 stays the default
(`lumen`, `lumen-ui`). Reuses ui_v3's AppState (the daemon seam) and ui_v2's
daemon_client / single_instance / tray; everything visual lives here.
"""

# Its own socket, so v4 can run beside ui_v3 ("lumen-ui-v3") and ui_v2
# ("lumen-ui") without taking over their show/quit commands.
SOCKET_NAME = "lumen-ui-v4"
