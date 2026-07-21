from lumen.daemon.connectors import local_files


def test_list_dir_sorts_dirs_first_case_insensitive(tmp_path):
    (tmp_path / "zeta.txt").write_text("z")
    (tmp_path / "Alpha.txt").write_text("a")
    (tmp_path / "beta").mkdir()
    (tmp_path / ".dot").write_text("d")
    got = local_files.list_dir(tmp_path)
    assert got["error"] is None
    assert [e["name"] for e in got["entries"]] == [
        "beta", ".dot", "Alpha.txt", "zeta.txt"]
    assert got["entries"][0]["is_dir"] is True
    assert got["parent"] == str(tmp_path.parent)
    assert got["truncated"] == 0


def test_list_dir_caps_and_reports_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(local_files, "LIST_MAX", 3)
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x")
    got = local_files.list_dir(tmp_path)
    assert len(got["entries"]) == 3
    assert got["truncated"] == 2


def test_list_dir_missing_folder_is_data_not_crash(tmp_path):
    got = local_files.list_dir(tmp_path / "nope")
    assert got["entries"] == []
    assert got["error"]


def test_read_text_roundtrip_and_guards(tmp_path):
    ok = tmp_path / "a.txt"
    ok.write_text("hello\nworld")
    assert local_files.read_text(ok) == {"content": "hello\nworld"}

    binary = tmp_path / "blob"
    binary.write_bytes(b"PK\x00\x01")
    assert local_files.read_text(binary) == {"error": "binary file"}

    latin = tmp_path / "latin.txt"
    latin.write_bytes("café".encode("latin-1"))
    assert local_files.read_text(latin) == {"error": "not UTF-8 text"}

    big = tmp_path / "big.txt"
    big.write_text("x" * 100)
    got = local_files.read_text(big, max_bytes=10)
    assert "too large" in got["error"]

    assert "error" in local_files.read_text(tmp_path / "missing.txt")


def test_dir_context_lists_names_and_sizes(tmp_path):
    (tmp_path / "notes.md").write_text("n" * 2048)
    (tmp_path / "sub").mkdir()
    text = local_files.dir_context(tmp_path)
    assert str(tmp_path) in text
    assert "- sub/ (folder)" in text
    assert "- notes.md (2.0 KB)" in text


def test_dir_context_honest_on_empty_and_error(tmp_path):
    assert "empty" in local_files.dir_context(tmp_path)
    assert "could not be listed" in local_files.dir_context(tmp_path / "gone")


def test_dir_context_truncation_note(tmp_path, monkeypatch):
    monkeypatch.setattr(local_files, "DIR_CONTEXT_MAX", 2)
    for i in range(4):
        (tmp_path / f"f{i}.txt").write_text("x")
    text = local_files.dir_context(tmp_path)
    assert "2 more entries not shown" in text


def test_file_context_caps_content_with_honest_note(tmp_path, monkeypatch):
    monkeypatch.setattr(local_files, "FILE_CONTEXT_MAX", 10)
    f = tmp_path / "long.txt"
    f.write_text("0123456789ABCDEF")
    text = local_files.file_context(f)
    assert "0123456789" in text and "ABCDEF" not in text
    assert "cut off" in text

    small = tmp_path / "small.txt"
    small.write_text("hi")
    assert "full content:\nhi" in local_files.file_context(small)

    blob = tmp_path / "blob"
    blob.write_bytes(b"\x00")
    assert "not readable as text" in local_files.file_context(blob)


def test_ask_context_combines_dir_and_open_file(tmp_path):
    f = tmp_path / "open.md"
    f.write_text("body")
    both = local_files.ask_context(tmp_path, f)
    assert "open in the Files screen" in both
    assert "open in the editor" in both and "body" in both
    solo = local_files.ask_context(tmp_path)
    assert "open in the editor" not in solo
