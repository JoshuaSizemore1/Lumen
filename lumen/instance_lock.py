"""Startup lock + the process sweep behind `lumen --quit`.

`lumen` cannot answer a hotkey press until its Qt server is listening, and
that is seconds after launch — the daemon has to be spawned and the whole UI
imported first. Two presses inside that window each saw "nothing running" and
started a full instance: two tray icons, two daemons, and (once the loser
stole the Qt socket name) a live instance no hotkey could reach again.

An exclusive `flock` held for the process lifetime covers that window. It is
deliberately not a pid file: the kernel drops it when the holder dies, however
it dies, so a crashed Lumen can never lock the next one out.
"""

import fcntl
import os
import signal
import time
from pathlib import Path

TERM_GRACE_S = 5.0

# Console scripts (`lumen`, `lumen-daemon`, `lumen-ui`) run as
# `…/python3 …/bin/lumen …`; everything else as `…/python3 -m lumen.<mod>`.
# Matching argv this way — rather than grepping the whole command line — keeps
# the sweep off shells and editors that merely mention lumen.
_MODULE_PREFIX = "lumen."


def lock_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "lumen" / "launch.lock"


def acquire(path=None):
    """Take the lock, or return None if another process holds it. Keep the
    returned handle alive: closing it releases the lock."""
    path = Path(path) if path is not None else lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release(handle) -> None:
    if handle is not None and hasattr(handle, "close"):
        handle.close()


def is_lumen_argv(argv: list[str]) -> bool:
    if not argv:
        return False
    if Path(argv[0]).name.startswith("lumen"):
        return True
    if len(argv) > 1 and Path(argv[1]).name.startswith("lumen"):
        return True
    return any(argv[i] == "-m" and argv[i + 1].startswith(_MODULE_PREFIX)
               for i in range(len(argv) - 1))


def lumen_pids() -> list[int]:
    """Our own Lumen processes — UI, daemon, MCP servers — minus this one."""
    me, uid = os.getpid(), os.getuid()
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            argv = entry.joinpath("cmdline").read_bytes().split(b"\0")
        except OSError:
            continue        # exited while we were looking
        if is_lumen_argv([a.decode(errors="replace") for a in argv if a]):
            found.append(int(entry.name))
    return found


def terminate(pids, grace: float = TERM_GRACE_S) -> None:
    """SIGTERM first — the daemon's clean-shutdown path — then insist."""
    for pid in pids:
        _signal(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in pids):
            return
        time.sleep(0.1)
    for pid in pids:
        if _alive(pid):
            _signal(pid, signal.SIGKILL)


def _signal(pid: int, sig) -> None:
    try:
        os.kill(pid, sig)
    except OSError:
        pass            # already gone


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
