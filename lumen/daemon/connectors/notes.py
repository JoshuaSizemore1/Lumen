"""Notes index: one configured folder of markdown/plain-text notes, chunked
and embedded into sqlite-vec inside the existing DB. Reindex is mtime-based
and runs on demand (the first notes question of a session picks up edits) —
no watcher loop, and the embedding model idle-unloads like everything else."""

import logging
import re
import sqlite3
from pathlib import Path

import sqlite_vec

log = logging.getLogger(__name__)

SUFFIXES = (".md", ".txt")
CHUNK_CHARS = 1200
DIM_KEY = "notes_vec_dim"
MODEL_KEY = "notes_embed_model"

_PARA = re.compile(r"\n\s*\n")


def chunk_text(text: str) -> list[str]:
    """Paragraphs merged up to ~CHUNK_CHARS; a monster paragraph hard-splits.
    Small chunks keep passages quotable and the narration prompt bounded."""
    paras = [p.strip() for p in _PARA.split(text or "") if p.strip()]
    chunks: list[str] = []
    current = ""
    for p in paras:
        while len(p) > CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(p[:CHUNK_CHARS])
            p = p[CHUNK_CHARS:].strip()
        if not p:
            continue
        if current and len(current) + len(p) + 2 > CHUNK_CHARS:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


class NotesStore:
    def __init__(self, conn: sqlite3.Connection, embedder, folder: Path,
                 model: str = "default"):
        """`embedder`: async (list[str]) -> list[list[float]] — the Ollama
        embed call in production, a fake in tests. `model` names it so a
        config change wipes and rebuilds the index instead of mixing
        incompatible vectors."""
        self._conn = conn
        self._embed = embedder
        self._folder = folder
        self._model = model
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS notes_files ("
                         "path TEXT PRIMARY KEY, mtime REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS notes_chunks ("
                         "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                         "path TEXT NOT NULL, content TEXT NOT NULL)")

    def file_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM notes_files").fetchone()[0]

    @property
    def folder(self) -> Path:
        return self._folder

    def _vec_dim(self) -> int | None:
        row = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (DIM_KEY,)).fetchone()
        return int(row["value"]) if row else None

    def _wipe(self) -> None:
        """Drop the whole index — the embedding model changed, and vectors
        from different models don't share a space."""
        with self._conn:
            self._conn.execute("DROP TABLE IF EXISTS notes_vec")
            self._conn.execute("DELETE FROM notes_chunks")
            self._conn.execute("DELETE FROM notes_files")
            self._conn.execute("DELETE FROM sync_state WHERE key = ?", (DIM_KEY,))

    def _ensure_vec(self, dim: int) -> None:
        if self._vec_dim() == dim:
            return
        with self._conn:
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec USING vec0("
                f"chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{dim}])")
            self._conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                (DIM_KEY, str(dim)))

    def _drop_file(self, path: str) -> None:
        ids = [r[0] for r in self._conn.execute(
            "SELECT id FROM notes_chunks WHERE path = ?", (path,))]
        with self._conn:
            if ids and self._vec_dim() is not None:
                self._conn.executemany("DELETE FROM notes_vec WHERE chunk_id = ?",
                                       [(i,) for i in ids])
            self._conn.execute("DELETE FROM notes_chunks WHERE path = ?", (path,))
            self._conn.execute("DELETE FROM notes_files WHERE path = ?", (path,))

    async def _index_file(self, path: str, mtime: float) -> None:
        self._drop_file(path)
        try:
            text = Path(path).read_text(errors="replace")
        except OSError:
            log.exception("could not read note %s", path)
            return
        chunks = chunk_text(text)
        if chunks:
            vectors = await self._embed(chunks)
            self._ensure_vec(len(vectors[0]))
            with self._conn:
                for content, vec in zip(chunks, vectors):
                    cur = self._conn.execute(
                        "INSERT INTO notes_chunks (path, content) VALUES (?, ?)",
                        (path, content))
                    self._conn.execute(
                        "INSERT INTO notes_vec (chunk_id, embedding) VALUES (?, ?)",
                        (cur.lastrowid, sqlite_vec.serialize_float32(vec)))
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO notes_files (path, mtime) VALUES (?, ?)",
                (path, mtime))

    async def reindex(self) -> dict:
        """Index new/changed files, drop removed ones. mtime comparison makes
        the no-change case cheap; only changed files pay for embeddings. A
        changed embedding model wipes the index first for a clean rebuild."""
        self._folder.mkdir(parents=True, exist_ok=True)
        stored = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (MODEL_KEY,)).fetchone()
        if stored is not None and stored["value"] != self._model:
            self._wipe()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                (MODEL_KEY, self._model))
        disk = {str(p): p.stat().st_mtime
                for p in sorted(self._folder.rglob("*"))
                if p.is_file() and p.suffix.lower() in SUFFIXES}
        known = {r["path"]: r["mtime"] for r in self._conn.execute(
            "SELECT path, mtime FROM notes_files")}
        removed = [p for p in known if p not in disk]
        changed = [p for p, mt in disk.items() if known.get(p) != mt]
        for p in removed:
            self._drop_file(p)
        for p in changed:
            await self._index_file(p, disk[p])
        return {"files": len(disk), "indexed": len(changed),
                "removed": len(removed)}

    async def search(self, query: str, k: int = 4) -> list[dict]:
        dim = self._vec_dim()
        if dim is None:
            return []
        vec = (await self._embed([query]))[0]
        if len(vec) != dim:
            return []          # model changed since indexing; next reindex heals
        rows = self._conn.execute(
            "SELECT c.path, c.content, v.distance FROM notes_vec v "
            "JOIN notes_chunks c ON c.id = v.chunk_id "
            "WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
            (sqlite_vec.serialize_float32(vec), k)).fetchall()
        return [{"path": r["path"], "content": r["content"],
                 "distance": r["distance"]} for r in rows]
