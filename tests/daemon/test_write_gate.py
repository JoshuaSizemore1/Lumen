import os

from lumen.daemon.write_gate import GrantStore


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
