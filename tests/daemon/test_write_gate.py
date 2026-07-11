import asyncio
import os

from lumen.daemon.config import MCPServerConfig
from lumen.daemon.confirm import ConfirmBroker
from lumen.daemon.write_gate import (DENIAL, GrantStore, WriteGate,
                                     confirm_payload, write_tools_map)


def store(tmp_path):
    return GrantStore(tmp_path / "write-grants.txt")


def test_missing_file_grants_nothing(tmp_path):
    assert store(tmp_path).is_granted("/tmp/x.txt") is False


def test_grant_then_check_roundtrip(tmp_path):
    s = store(tmp_path)
    s.grant(str(tmp_path / "notes.txt"))
    assert s.is_granted(str(tmp_path / "notes.txt")) is True
    assert s.is_granted(str(tmp_path / "other.txt")) is False


def test_grant_twice_appends_no_duplicate(tmp_path):
    s = store(tmp_path)
    s.grant(str(tmp_path / "notes.txt"))
    s.grant(str(tmp_path / "notes.txt"))
    lines = (tmp_path / "write-grants.txt").read_text().splitlines()
    assert lines == [str(tmp_path / "notes.txt")]


def test_symlink_resolves_to_granted_target(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("x")
    link = tmp_path / "alias.txt"
    os.symlink(target, link)
    s = store(tmp_path)
    s.grant(str(target))
    assert s.is_granted(str(link)) is True


def test_dotdot_traversal_normalized(tmp_path):
    (tmp_path / "sub").mkdir()
    s = store(tmp_path)
    s.grant(str(tmp_path / "notes.txt"))
    assert s.is_granted(str(tmp_path / "sub" / ".." / "notes.txt")) is True


def test_relative_path_never_granted_nor_recorded(tmp_path):
    s = store(tmp_path)
    s.grant("notes.txt")
    assert not (tmp_path / "write-grants.txt").exists()
    assert s.is_granted("notes.txt") is False


def test_hand_edit_revocation_takes_effect_immediately(tmp_path):
    s = store(tmp_path)
    s.grant(str(tmp_path / "a.txt"))
    s.grant(str(tmp_path / "b.txt"))
    assert s.is_granted(str(tmp_path / "a.txt")) is True
    # revoke by deleting the line, as the user would
    (tmp_path / "write-grants.txt").write_text(str(tmp_path / "b.txt") + "\n")
    assert s.is_granted(str(tmp_path / "a.txt")) is False
    assert s.is_granted(str(tmp_path / "b.txt")) is True


def test_blank_lines_ignored(tmp_path):
    (tmp_path / "write-grants.txt").write_text(
        f"\n{tmp_path / 'a.txt'}\n\n  \n")
    assert store(tmp_path).is_granted(str(tmp_path / "a.txt")) is True


# --- write_tools_map -----------------------------------------------------

def test_write_tools_map_unions_across_servers():
    servers = (
        MCPServerConfig("fs", "npx", write_tools={"write_file": ("path",)}),
        MCPServerConfig("other", "x",
                        write_tools={"write_file": ("file",),
                                     "move_file": ("source", "destination")}),
    )
    m = write_tools_map(servers)
    assert set(m["write_file"]) == {"path", "file"}
    assert m["move_file"] == ("source", "destination")


# --- confirm_payload -----------------------------------------------------

def test_confirm_payload_write_file_rows_and_preview():
    p = confirm_payload("write_file",
                        {"path": "/tmp/x.txt", "content": "c" * 600}, ("path",))
    assert p["title"] == "Write file"
    assert p["confirm_label"] == "Allow write"
    rows = dict(p["rows"])
    assert rows["Path"] == "/tmp/x.txt"
    assert rows["Content"].startswith("c" * 500)
    assert "600 chars" in rows["Content"]
    assert "future writes" in p["intro"]


def test_confirm_payload_move_labels_both_paths():
    p = confirm_payload("move_file",
                        {"source": "/a.txt", "destination": "/b.txt"},
                        ("source", "destination"))
    rows = dict(p["rows"])
    assert p["title"] == "Move or rename"
    assert rows["Source"] == "/a.txt" and rows["Destination"] == "/b.txt"


def test_confirm_payload_unknown_tool_falls_back_to_name():
    p = confirm_payload("mystery_tool", {"path": "/x"}, ("path",))
    assert p["title"] == "mystery_tool"


# --- WriteGate -----------------------------------------------------------

def gate_fixture(tmp_path, write_tools=None, timeout=5.0):
    broker = ConfirmBroker(timeout=timeout)
    grants = GrantStore(tmp_path / "g.txt")
    gate = WriteGate(grants, broker,
                     write_tools if write_tools is not None
                     else {"write_file": ("path",),
                           "move_file": ("source", "destination")})
    emitted = []

    async def emit(ev):
        emitted.append(ev)

    return gate, grants, broker, emitted, emit


def answer(broker, emitted, approved):
    async def _answer():
        while not emitted:
            await asyncio.sleep(0)
        broker.resolve(emitted[-1]["confirm_id"], approved)
    return asyncio.ensure_future(_answer())


async def test_read_tool_passes_without_emit(tmp_path):
    gate, _, _, emitted, emit = gate_fixture(tmp_path)
    assert await gate.check("read_file", {"path": "/etc/passwd"}, emit) is None
    assert emitted == []


async def test_granted_write_passes_without_emit(tmp_path):
    gate, grants, _, emitted, emit = gate_fixture(tmp_path)
    grants.grant(str(tmp_path / "f.txt"))
    result = await gate.check("write_file",
                              {"path": str(tmp_path / "f.txt")}, emit)
    assert result is None
    assert emitted == []


async def test_ungranted_write_approve_executes_and_records(tmp_path):
    gate, grants, broker, emitted, emit = gate_fixture(tmp_path)
    t = answer(broker, emitted, True)
    result = await gate.check(
        "write_file", {"path": str(tmp_path / "f.txt"), "content": "hi"}, emit)
    await t
    assert result is None
    assert grants.is_granted(str(tmp_path / "f.txt")) is True
    assert "confirm_request" in emitted[0] and "confirm_id" in emitted[0]
    assert dict(emitted[0]["confirm_request"]["rows"])["Content"] == "hi"


async def test_ungranted_write_deny_returns_denial_records_nothing(tmp_path):
    gate, grants, broker, emitted, emit = gate_fixture(tmp_path)
    t = answer(broker, emitted, False)
    result = await gate.check(
        "write_file", {"path": str(tmp_path / "f.txt"), "content": "hi"}, emit)
    await t
    assert result == DENIAL
    assert grants.is_granted(str(tmp_path / "f.txt")) is False


async def test_move_gates_and_grants_both_paths(tmp_path):
    gate, grants, broker, emitted, emit = gate_fixture(tmp_path)
    t = answer(broker, emitted, True)
    result = await gate.check(
        "move_file", {"source": str(tmp_path / "a.txt"),
                      "destination": str(tmp_path / "b.txt")}, emit)
    await t
    assert result is None
    assert grants.is_granted(str(tmp_path / "a.txt")) is True
    assert grants.is_granted(str(tmp_path / "b.txt")) is True


async def test_move_with_one_granted_path_still_confirms(tmp_path):
    gate, grants, broker, emitted, emit = gate_fixture(tmp_path)
    grants.grant(str(tmp_path / "a.txt"))
    t = answer(broker, emitted, True)
    result = await gate.check(
        "move_file", {"source": str(tmp_path / "a.txt"),
                      "destination": str(tmp_path / "b.txt")}, emit)
    await t
    assert result is None and len(emitted) == 1


async def test_missing_path_arg_still_confirms(tmp_path):
    gate, _, broker, emitted, emit = gate_fixture(tmp_path)
    t = answer(broker, emitted, True)
    result = await gate.check("write_file", {"content": "hi"}, emit)
    await t
    assert result is None
    assert len(emitted) == 1   # fail-safe: unclassifiable write still asks


async def test_namespaced_tool_name_matches(tmp_path):
    gate, grants, broker, emitted, emit = gate_fixture(tmp_path)
    t = answer(broker, emitted, False)
    result = await gate.check(
        "fs__write_file", {"path": str(tmp_path / "f.txt")}, emit)
    await t
    assert result == DENIAL


async def test_relative_path_approval_executes_but_records_nothing(tmp_path):
    gate, grants, broker, emitted, emit = gate_fixture(tmp_path)
    t = answer(broker, emitted, True)
    result = await gate.check("write_file", {"path": "notes.txt"}, emit)
    await t
    assert result is None
    assert not (tmp_path / "g.txt").exists()
