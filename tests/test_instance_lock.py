"""The startup lock. `lumen` spends seconds spawning a daemon and importing
the UI before its Qt server can answer anything — a second hotkey press in
that window used to start a whole rival instance (two tray icons, two
daemons). The lock covers that window; the Qt name guard covers the rest.
"""
import os
import subprocess
import sys
import textwrap

from lumen import instance_lock


def test_acquire_then_second_attempt_fails(tmp_path):
    held = instance_lock.acquire(tmp_path / "l.lock")
    try:
        assert held is not None
        assert instance_lock.acquire(tmp_path / "l.lock") is None
    finally:
        instance_lock.release(held)


def test_release_frees_it(tmp_path):
    path = tmp_path / "l.lock"
    instance_lock.release(instance_lock.acquire(path))
    second = instance_lock.acquire(path)
    assert second is not None
    instance_lock.release(second)


def test_lock_is_held_against_another_process(tmp_path):
    """flock is per-open-file-description, so a same-process check can pass
    while a real second `lumen` still gets in."""
    path = tmp_path / "l.lock"
    held = instance_lock.acquire(path)
    try:
        probe = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(f"""
                from lumen import instance_lock
                print(instance_lock.acquire({str(path)!r}) is None)
            """)], capture_output=True, text=True, timeout=30)
        assert probe.stdout.strip() == "True", probe.stderr
    finally:
        instance_lock.release(held)


def test_dead_holder_releases_the_lock(tmp_path):
    """A killed instance must not lock Lumen out forever — flock dies with the
    process, which is why this is a lock file and not a pid file."""
    path = tmp_path / "l.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            from lumen import instance_lock
            held = instance_lock.acquire({str(path)!r})   # must stay referenced
            assert held is not None
            print("held", flush=True)
            time.sleep(60)
        """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert instance_lock.acquire(path) is None
    finally:
        holder.kill()
        holder.wait()
    mine = instance_lock.acquire(path)
    assert mine is not None
    instance_lock.release(mine)


def test_default_path_is_under_the_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert instance_lock.lock_path() == tmp_path / "lumen" / "launch.lock"


def test_acquire_creates_the_parent_directory(tmp_path):
    path = tmp_path / "fresh" / "launch.lock"
    held = instance_lock.acquire(path)
    assert held is not None
    assert path.exists()
    instance_lock.release(held)


def test_lumen_processes_exclude_this_one_and_unrelated_commands():
    """The sweep behind `lumen --quit` must never reach for a shell that
    merely mentions lumen."""
    assert instance_lock.is_lumen_argv(
        ["/v/bin/python3", "/v/bin/lumen", "--toggle-launcher"]) is True
    assert instance_lock.is_lumen_argv(
        ["/v/bin/python3", "-m", "lumen.daemon"]) is True
    assert instance_lock.is_lumen_argv(
        ["/v/bin/python", "-m", "lumen.mcp_servers.gcal"]) is True
    assert instance_lock.is_lumen_argv(["grep", "-i", "lumen"]) is False
    assert instance_lock.is_lumen_argv(
        ["/v/bin/python", "-m", "pytest", "tests/daemon"]) is False
    assert instance_lock.is_lumen_argv(["zsh", "-c", "tail lumen.daemon.log"]) is False
    assert instance_lock.is_lumen_argv([]) is False
    assert os.getpid() not in instance_lock.lumen_pids()
