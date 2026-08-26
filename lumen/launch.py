"""Unified launcher: `lumen` starts the daemon (unless one is already running),
runs the UI in-process, and stops the daemon it started when the UI exits.
In this mode closing the main window quits the whole app (LUMEN_UNIFIED).
A daemon that was already running (systemd, `uv run lumen-daemon`) is left
alone on exit — the launcher only stops what it started.

`lumen --quit` is the manual kill switch: it asks a running instance to quit
and then sweeps anything left behind, including an instance that has lost its
socket and can no longer be reached any other way.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from lumen import instance_lock

START_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 10.0
HANDOFF_TIMEOUT_S = 25.0    # covers a cold daemon start (START_TIMEOUT_S) + UI
HANDOFF_POLL_S = 0.1


def daemon_alive(socket_path) -> bool:
    """A stale socket file refuses the connect — only a live daemon accepts."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(1.0)
    try:
        s.connect(str(socket_path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _die_with_parent() -> None:
    """Child-side (pre-exec): SIGTERM the daemon when the launcher vanishes —
    even a crashed/killed launcher then keeps the 'UI gone → daemon off'
    promise, with no orphan left syncing in the background."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


def daemon_log_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "lumen" / "daemon.log"


def spawn_daemon() -> subprocess.Popen:
    # MCP servers configured as `python -m …` must resolve against this venv,
    # wherever the launcher itself was invoked from.
    env = dict(os.environ)
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    log_path = daemon_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log:
        return subprocess.Popen([sys.executable, "-m", "lumen.daemon"],
                                stdout=log, stderr=log, env=env,
                                preexec_fn=_die_with_parent)


def wait_for_socket(socket_path, proc, timeout: float = START_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon_alive(socket_path):
            return True
        if proc.poll() is not None:
            return False        # daemon exited before the socket came up
        time.sleep(0.2)
    return False


def stop_daemon(proc) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()            # the daemon's SIGTERM path is a clean shutdown
    try:
        proc.wait(STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def quit_running() -> None:
    """`lumen --quit`. Ask first, then sweep: an instance whose socket name was
    taken over answers nothing, and used to be unkillable short of hunting it
    down in `ps`."""
    from lumen.ui_v2.single_instance import try_send
    from lumen.ui_v3 import SOCKET_NAME
    asked = try_send(SOCKET_NAME, "quit")
    if asked:
        print("lumen: asked the running instance to quit")
        deadline = time.monotonic() + STOP_TIMEOUT_S
        while instance_lock.lumen_pids() and time.monotonic() < deadline:
            time.sleep(HANDOFF_POLL_S)
    left = instance_lock.lumen_pids()
    if left:
        instance_lock.terminate(left)
        print(f"lumen: stopped {len(left)} leftover process(es)")
    elif not asked:
        print("lumen: nothing running")


def main(handoff_timeout: float = HANDOFF_TIMEOUT_S) -> None:
    from lumen.ui_v2.single_instance import try_send   # shared IPC plumbing
    from lumen.ui_v3 import SOCKET_NAME                 # cheap: no UI stack imported
    if "--quit" in sys.argv:
        quit_running()
        return

    toggle = "--toggle-launcher" in sys.argv
    command = "toggle-launcher" if toggle else "show"
    if try_send(SOCKET_NAME, command):
        return   # already running — it handled the command; its daemon is its own

    # A press we answer late is impatience with a slow start, not a dismissal:
    # summon, never toggle, or the second press closes the launcher the first
    # one just opened.
    waited_command = "show-launcher" if toggle else "show"
    lock = instance_lock.acquire()
    deadline = time.monotonic() + handoff_timeout
    while lock is None:
        # An instance is *starting* — seconds of daemon spawn and UI import
        # before it can answer anything. Waiting for its server is the whole
        # point: two presses in that window used to start two full instances,
        # and the loser stole the Qt socket name from the winner.
        if try_send(SOCKET_NAME, waited_command):
            return
        if time.monotonic() >= deadline:
            print("lumen: another instance is still starting up — gave up",
                  file=sys.stderr)
            return
        time.sleep(HANDOFF_POLL_S)
        lock = instance_lock.acquire()   # it died mid-start: we take over

    try:
        from lumen.daemon.config import load_config
        cfg = load_config()
        proc = None
        if not daemon_alive(cfg.socket_path):
            proc = spawn_daemon()
            if not wait_for_socket(cfg.socket_path, proc):
                stop_daemon(proc)
                raise SystemExit(
                    f"lumen: the daemon did not come up — see {daemon_log_path()}")

        os.environ["LUMEN_UNIFIED"] = "1"   # closing the window quits the app
        from lumen.ui_v3.app import main as ui_main
        try:
            ui_main()   # blocks until quit; exits via SystemExit with the UI's code
        finally:
            if proc is not None:
                stop_daemon(proc)
    finally:
        instance_lock.release(lock)


if __name__ == "__main__":
    main()
