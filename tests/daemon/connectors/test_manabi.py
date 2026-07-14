"""Manabi nudge: read the last-review signal from Manabi's own DB, read-only.
Zero SRS logic — one timestamp compared to today."""

import sqlite3
from datetime import datetime, timedelta, timezone

from lumen.daemon.connectors.manabi import ManabiStatus

NOW = datetime.fromisoformat("2026-07-14T09:00:00-06:00")


def manabi_db(tmp_path, reviews=(), kana=()):
    p = tmp_path / "nihongo.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE reviews (id INTEGER PRIMARY KEY, grade INTEGER, "
                 "reviewed TEXT)")
    conn.execute("CREATE TABLE kana_sessions (id INTEGER PRIMARY KEY, "
                 "started TEXT)")
    conn.executemany("INSERT INTO reviews (grade, reviewed) VALUES (1, ?)",
                     [(r,) for r in reviews])
    conn.executemany("INSERT INTO kana_sessions (started) VALUES (?)",
                     [(k,) for k in kana])
    conn.commit()
    conn.close()
    return p


def test_unconfigured_and_missing_file_are_not_due(tmp_path):
    s = ManabiStatus(None)
    assert s.status(NOW) == {"configured": False, "due": None, "last_review": None}
    s = ManabiStatus(tmp_path / "nope.db")
    assert s.status(NOW)["configured"] is False


def test_no_reviews_at_all_is_due(tmp_path):
    s = ManabiStatus(manabi_db(tmp_path))
    st = s.status(NOW)
    assert st["configured"] is True and st["due"] is True
    assert st["last_review"] is None


def test_reviewed_today_clears_the_nudge(tmp_path):
    # Manabi stores datetime('now') = UTC; 14:30 UTC is 08:30 local (-06:00)
    s = ManabiStatus(manabi_db(tmp_path, reviews=["2026-07-14 14:30:00"]))
    st = s.status(NOW)
    assert st["due"] is False
    assert st["last_review"].startswith("2026-07-14")


def test_reviewed_yesterday_is_due(tmp_path):
    s = ManabiStatus(manabi_db(tmp_path, reviews=["2026-07-13 20:00:00"]))
    assert s.status(NOW)["due"] is True


def test_kana_sessions_count_as_study(tmp_path):
    s = ManabiStatus(manabi_db(tmp_path, reviews=["2026-07-10 10:00:00"],
                               kana=["2026-07-14 14:00:00"]))
    assert s.status(NOW)["due"] is False


def test_utc_to_local_date_boundary(tmp_path):
    # 01:00 UTC on the 14th is 19:00 on the 13th local (-06:00) -> still due
    s = ManabiStatus(manabi_db(tmp_path, reviews=["2026-07-14 01:00:00"]))
    assert s.status(NOW)["due"] is True


def test_broken_db_degrades_to_unconfigured(tmp_path):
    p = tmp_path / "garbage.db"
    p.write_text("not a database")
    st = ManabiStatus(p).status(NOW)
    assert st["due"] is None                        # never nags on a bad signal
