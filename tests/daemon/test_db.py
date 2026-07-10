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


def test_books_tables_exist_with_defaults(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO books (title, created_at) VALUES ('Dune', '2026-07-09T10:00:00')")
    conn.execute(
        "INSERT INTO book_recs (title, author, rationale, generated_at) "
        "VALUES ('Solaris', 'Stanislaw Lem', 'because', '2026-07-09T10:00:00')")
    conn.commit()
    book = conn.execute("SELECT * FROM books").fetchone()
    assert book["title"] == "Dune" and book["tags"] == "[]"
    assert book["author"] is None and book["rating"] is None
    rec = conn.execute("SELECT * FROM book_recs").fetchone()
    assert rec["author"] == "Stanislaw Lem" and rec["generated_at"]
    conn.close()
