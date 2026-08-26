"""OS-keyring storage for the Canvas login (uNID + password), UI-side only so the
password never reaches the daemon or a file. Backed by the Secret Service (GNOME
Keyring) via the `keyring` lib. Autofill convenience only — a miss (no entry, or a
locked collection) is non-fatal: load() just returns None.

Every call is self-guarded. These run inside Qt slots, where an exception is
swallowed by the event loop and the user sees nothing happen at all — which is
indistinguishable from "the autofill selectors missed" and sent the #44
investigation down the wrong path once already. A locked collection or an absent
D-Bus session must degrade to "type it yourself", never to a silent traceback."""

import logging

import keyring
import keyring.errors

log = logging.getLogger("lumen.ui")

_SERVICE = "lumen-canvas"


def save(unid: str, password: str) -> bool:
    """True if the credentials were stored. False on any backend trouble."""
    try:
        keyring.set_password(_SERVICE, "unid", unid)
        keyring.set_password(_SERVICE, "password", password)
        return True
    except Exception:
        log.exception("canvas keyring save failed — login not remembered")
        return False


def load() -> tuple[str, str] | None:
    try:
        unid = keyring.get_password(_SERVICE, "unid")
        password = keyring.get_password(_SERVICE, "password")
    except Exception:
        log.exception("canvas keyring read failed — autofill unavailable")
        return None
    if not unid or password is None:
        return None
    return unid, password


def forget() -> bool:
    ok = True
    for key in ("unid", "password"):
        try:
            keyring.delete_password(_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass          # already absent — a no-op
        except Exception:
            log.exception("canvas keyring delete failed")
            ok = False
    return ok
