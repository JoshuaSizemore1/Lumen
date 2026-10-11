"""Field Notes redesign of the Lumen UI (v4).

The primary Lumen UI as of 2026-10-10: `lumen`, `lumen-ui`, `lumen-ui-v4` and
`python -m lumen.ui_v4` all launch it; ui_v3 stays runnable as `lumen-ui-v3`.
Reuses ui_v3's AppState (the daemon seam) and ui_v2's daemon_client /
single_instance / tray; everything visual lives here.
"""

# Its own socket, so v4 can run beside ui_v3 ("lumen-ui-v3") and ui_v2
# ("lumen-ui") without taking over their show/quit commands.
SOCKET_NAME = "lumen-ui-v4"
