"""Notes index: mtime-based reindex into sqlite-vec, chunking, KNN search.
A fake embedder keeps tests deterministic; real embeddings are live-verify."""

from lumen.daemon import db
from lumen.daemon.connectors.notes import NotesStore, chunk_text


class FakeEmbedder:
    """[1,0] for router-flavored text, [0,1] otherwise — enough for KNN."""

    def __init__(self, dim=2):
        self.dim = dim
        self.calls = 0

    async def __call__(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * self.dim
            v[0 if "router" in t.lower() else 1] = 1.0
            out.append(v)
        return out


def make_store(tmp_path, embedder=None, model="fake"):
    folder = tmp_path / "Notes"
    conn = db.connect(tmp_path / "n.db")
    return NotesStore(conn, embedder or FakeEmbedder(), folder, model), folder


# ---- chunking ----

def test_chunks_merge_paragraphs_up_to_cap():
    text = "para one.\n\npara two.\n\npara three."
    assert chunk_text(text) == ["para one.\n\npara two.\n\npara three."]


def test_chunks_split_at_cap_and_hard_split_long_paragraphs():
    paras = "\n\n".join(f"paragraph {i} " + "x" * 500 for i in range(5))
    chunks = chunk_text(paras)
    assert len(chunks) > 1
    assert all(len(c) <= 1300 for c in chunks)
    monster = "y" * 3000
    assert all(len(c) <= 1300 for c in chunk_text(monster))


def test_empty_text_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


# ---- reindex ----

async def test_reindex_creates_folder_and_indexes_files(tmp_path):
    store, folder = make_store(tmp_path)
    res = await store.reindex()
    assert folder.is_dir() and res == {"files": 0, "indexed": 0, "removed": 0}
    (folder / "net.md").write_text("The router password is hunter2.")
    (folder / "garden.txt").write_text("Plant tomatoes in May.")
    (folder / "skip.pdf").write_text("not indexed")
    res = await store.reindex()
    assert res["files"] == 2 and res["indexed"] == 2


async def test_reindex_skips_unchanged_files(tmp_path):
    emb = FakeEmbedder()
    store, folder = make_store(tmp_path, emb)
    (folder).mkdir()
    (folder / "net.md").write_text("router notes")
    await store.reindex()
    calls = emb.calls
    res = await store.reindex()
    assert emb.calls == calls and res["indexed"] == 0


async def test_reindex_reembeds_changed_and_drops_removed(tmp_path):
    import os
    store, folder = make_store(tmp_path)
    folder.mkdir()
    f = folder / "net.md"
    f.write_text("router notes")
    await store.reindex()
    f.write_text("garden notes now")
    os.utime(f, (1e9, 1e9))          # force a different mtime
    res = await store.reindex()
    assert res["indexed"] == 1
    hits = await store.search("router password")
    assert all("garden" in h["content"] for h in hits)   # old chunk gone
    f.unlink()
    res = await store.reindex()
    assert res["removed"] == 1
    assert await store.search("garden") == []


# ---- search ----

async def test_search_returns_nearest_chunks_with_paths(tmp_path):
    store, folder = make_store(tmp_path)
    folder.mkdir()
    (folder / "net.md").write_text("The router password is hunter2.")
    (folder / "garden.md").write_text("Plant tomatoes in May.")
    await store.reindex()
    hits = await store.search("what's the router password?", k=1)
    assert len(hits) == 1
    assert hits[0]["path"].endswith("net.md")
    assert "hunter2" in hits[0]["content"]


async def test_search_before_any_index_is_empty(tmp_path):
    store, _ = make_store(tmp_path)
    assert await store.search("anything") == []


async def test_model_change_forces_clean_reindex(tmp_path):
    store, folder = make_store(tmp_path, FakeEmbedder(dim=2), model="m1")
    folder.mkdir()
    (folder / "net.md").write_text("router notes")
    await store.reindex()
    # a different embedding model (new name, new dim) wipes and rebuilds
    store2 = NotesStore(store._conn, FakeEmbedder(dim=3), folder, "m2")
    res = await store2.reindex()
    assert res["indexed"] == 1                      # re-embedded from scratch
    hits = await store2.search("router", k=1)
    assert hits and "router" in hits[0]["content"]


def test_file_count(tmp_path):
    store, _ = make_store(tmp_path)
    assert store.file_count() == 0
