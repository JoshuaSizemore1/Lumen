"""OS-keyring storage for the Canvas login (uNID + password), UI-side only so the
password never reaches the daemon or a file. Backed by the Secret Service (GNOME
Keyring) via the `keyring` lib. Autofill convenience only — a miss (no entry, or a
locked collection) is non-fatal: load() just returns None."""

import keyring
import keyring.errors

_SERVICE = "lumen-canvas"


def save(unid: str, password: str) -> None:
    keyring.set_password(_SERVICE, "unid", unid)
    keyring.set_password(_SERVICE, "password", password)


def load() -> tuple[str, str] | None:
    unid = keyring.get_password(_SERVICE, "unid")
    password = keyring.get_password(_SERVICE, "password")
    if not unid or password is None:
        return None
    return unid, password


def forget() -> None:
    for key in ("unid", "password"):
        try:
            keyring.delete_password(_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass          # already absent — a no-op
