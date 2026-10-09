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
import threading

import keyring
import keyring.errors

log = logging.getLogger("lumen.ui")

_SERVICE = "lumen-canvas"

# Every keyring call in this process goes through this lock. The Secret
# Service backend talks D-Bus through `jeepney`, whose connection is NOT safe
# to use from two threads at once — two probes racing segfaulted the process
# outright. One lock, and the background probe below can never overlap a
# foreground load/save/forget.
_lock = threading.RLock()
_probing = False
_waiters: list = []

# Whether a login is stored — the only thing the Canvas screen needs while it
# renders, and the one thing that must not cost a D-Bus round trip to find out.
# Measured: the first keyring call in a process costs ~146 ms (backend plugin
# discovery plus two Secret Service round trips), and `_connect_hero` made it
# on every content render, on the GUI thread. That was the Canvas tab's freeze
# (#61) — not layout, not Chromium. None means "not probed yet".
_has_saved: bool | None = None


def has_saved(default=False):
    """Is a Canvas login stored? Cached, and free after the first answer.

    Returns `default` until `prime()` has reported, so a render never blocks
    waiting on the keyring. Pass `default=None` to tell "not probed yet" apart
    from "probed, and there is nothing saved"."""
    return default if _has_saved is None else _has_saved


def prime(done=None) -> None:
    """Probe the keyring on a worker thread and cache the answer.

    D-Bus on the GUI thread is the thing to avoid: a locked collection can hang
    for seconds, or raise an unlock prompt, in the middle of a paint. `done` (if
    given) is called with the bool FROM THE WORKER THREAD — marshal it back to
    Qt yourself.

    At most one probe ever runs. Callers that arrive while one is in flight are
    queued onto it rather than starting their own."""
    global _probing
    with _lock:
        if _has_saved is not None:
            cached = _has_saved
        else:
            cached = None
            if done is not None:
                _waiters.append(done)
            if _probing:
                return              # already on its way; the queue will answer
            _probing = True
    if cached is not None:
        if done is not None:
            done(cached)
        return
    threading.Thread(target=_probe, name="canvas-keyring-probe",
                     daemon=True).start()


def _probe() -> None:
    global _has_saved, _probing
    try:
        with _lock:
            value = keyring.get_password(_SERVICE, "unid") is not None
    except Exception:
        log.exception("canvas keyring probe failed — assuming no saved login")
        value = False
    with _lock:
        _has_saved = value
        _probing = False
        waiting, _waiters[:] = list(_waiters), []
    for cb in waiting:
        cb(value)


def save(unid: str, password: str) -> bool:
    """True if the credentials were stored. False on any backend trouble."""
    global _has_saved
    try:
        with _lock:
            keyring.set_password(_SERVICE, "unid", unid)
            keyring.set_password(_SERVICE, "password", password)
        _has_saved = True
        return True
    except Exception:
        log.exception("canvas keyring save failed — login not remembered")
        return False


def load() -> tuple[str, str] | None:
    try:
        with _lock:
            unid = keyring.get_password(_SERVICE, "unid")
            password = keyring.get_password(_SERVICE, "password")
    except Exception:
        log.exception("canvas keyring read failed — autofill unavailable")
        return None
    if not unid or password is None:
        return None
    return unid, password


def forget() -> bool:
    global _has_saved
    ok = True
    for key in ("unid", "password"):
        try:
            with _lock:
                keyring.delete_password(_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass          # already absent — a no-op
        except Exception:
            log.exception("canvas keyring delete failed")
            ok = False
    _has_saved = False
    return ok
