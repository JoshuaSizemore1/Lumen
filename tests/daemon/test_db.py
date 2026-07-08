from lumen.daemon import db


def test_connect_creates_parent_dir_and_schema(tmp_path):
    conn = db.connect(tmp_path / "data" / "lumen.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(todos)")}
    assert cols == {"id", "text", "due_date", "completed", "created_at", "source", "tags"}
    conn.close()


def test_connect_is_idempotent(tmp_path):
    path = tmp_path / "lumen.db"
    db.connect(path).close()
    conn = db.connect(path)  # second open: CREATE IF NOT EXISTS must not fail
    assert conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0] == 0
    conn.close()


def test_wal_mode_and_row_factory(tmp_path):
    conn = db.connect(tmp_path / "lumen.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.execute(
        "INSERT INTO todos (text, created_at) VALUES ('x', '2026-07-08T10:00:00')")
    row = conn.execute("SELECT * FROM todos").fetchone()
    assert row["text"] == "x" and row["tags"] == "[]" and row["source"] == "manual"
    conn.close()
