"""Persisted Canvas session (Workstream P). The file holds a bearer credential,
so the mode matters as much as the round-trip."""

import json
import stat

from lumen.daemon.connectors import canvas_session

COOKIES = {"canvas_session": "abc123", "_csrf_token": "t"}


def test_round_trip(tmp_path):
    path = tmp_path / "session.json"
    assert canvas_session.save(path, COOKIES) is True
    assert canvas_session.load(path) == COOKIES


def test_save_creates_the_parent_directory(tmp_path):
    path = tmp_path / "canvas" / "nested" / "session.json"
    assert canvas_session.save(path, COOKIES) is True
    assert canvas_session.load(path) == COOKIES


def test_saved_file_is_owner_only(tmp_path):
    path = tmp_path / "session.json"
    canvas_session.save(path, COOKIES)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_records_a_timestamp(tmp_path):
    path = tmp_path / "session.json"
    canvas_session.save(path, COOKIES)
    assert json.loads(path.read_text())["saved_at"]


def test_save_refuses_an_empty_jar(tmp_path):
    path = tmp_path / "session.json"
    assert canvas_session.save(path, {}) is False
    assert not path.exists()


def test_load_missing_file_is_none(tmp_path):
    assert canvas_session.load(tmp_path / "nope.json") is None


def test_load_garbage_is_none(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("\x00 not json at all {{{")
    assert canvas_session.load(path) is None


def test_load_wrong_shape_is_none(tmp_path):
    """Valid JSON that isn't a session must not become one."""
    for payload in ('["a", "b"]', '{"cookies": "a string"}', '{"cookies": {}}',
                    '{}', 'null', '42'):
        path = tmp_path / "session.json"
        path.write_text(payload)
        assert canvas_session.load(path) is None, payload


def test_load_stringifies_values(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"cookies": {"canvas_session": 12345}}))
    assert canvas_session.load(path) == {"canvas_session": "12345"}


def test_clear_removes_the_file(tmp_path):
    path = tmp_path / "session.json"
    canvas_session.save(path, COOKIES)
    canvas_session.clear(path)
    assert not path.exists()
    assert canvas_session.load(path) is None


def test_clear_on_an_absent_file_is_a_noop(tmp_path):
    canvas_session.clear(tmp_path / "never-existed.json")   # must not raise


def test_save_on_an_unwritable_path_returns_false(tmp_path):
    unwritable = tmp_path / "file"
    unwritable.write_text("i am a file, not a directory")
    assert canvas_session.save(unwritable / "session.json", COOKIES) is False
