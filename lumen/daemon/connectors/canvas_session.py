"""Persist the Canvas session cookies so a daemon restart doesn't force a fresh
CAS + Duo login.

Storage is a mode-0600 JSON file, matching `google_auth.save_token` — which
already keeps the Google refresh token, a strictly more powerful credential
(Gmail *and* Calendar read/write, no expiry), the same way in the same place.
Deliberately NOT the OS keyring: the daemon can be started by systemd before the
Secret Service is unlocked, and a keyring read would then return None and bring
the daemon up silently disconnected — exactly the failure this module exists to
remove. A file has no unlock ordering to lose.

The password is NOT here and never reaches the daemon; it stays UI-side in the
keyring (see ui_v3/canvas_creds.py). Only the session cookie crosses over, and
only for the Canvas host itself — the UI filters the jar before handing it off.

Every function is failure-tolerant: a missing, corrupt, or wrong-shaped file
means "no session", never a crash on daemon start."""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lumen.daemon")


def save(path: Path, cookies: dict[str, str]) -> bool:
    """Write the jar 0600. False on any I/O trouble — a session we merely failed
    to persist still works for this run of the daemon."""
    if not cookies:
        return False
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "cookies": {str(k): str(v) for k, v in cookies.items()},
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }))
        path.chmod(0o600)
        return True
    except OSError:
        log.exception("canvas session save failed — session kept in memory only")
        return False


def load(path: Path) -> dict[str, str] | None:
    """The stored jar, or None. A restored session is UNVALIDATED — the first
    sync is what proves it, and a dead one clears itself (see CanvasSync)."""
    try:
        raw = Path(path).read_text()
    except (OSError, ValueError):
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.info("canvas session file is unreadable — starting disconnected")
        return None
    if not isinstance(obj, dict):
        return None
    cookies = obj.get("cookies")
    if not isinstance(cookies, dict) or not cookies:
        return None
    out = {str(k): str(v) for k, v in cookies.items() if isinstance(k, str)}
    return out or None


def clear(path: Path) -> None:
    """Delete the stored session. Already-absent is the desired state, not an
    error."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("canvas session file could not be removed")
