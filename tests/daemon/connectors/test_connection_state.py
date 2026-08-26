"""ConnectionState — persistent per-connection enable/disable (#38)."""
from lumen.daemon import db
from lumen.daemon.connectors.connection_state import ConnectionState


def test_absent_row_is_enabled_then_toggles_persist(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    cs = ConnectionState(conn)
    assert cs.enabled("gmail") is True           # nothing stored yet -> on
    cs.set_enabled("gmail", False)
    assert cs.enabled("gmail") is False
    assert cs.all()["gmail"] is False
    cs.set_enabled("gmail", True)
    assert cs.enabled("gmail") is True


def test_state_survives_reopen(tmp_path):
    p = tmp_path / "c.db"
    ConnectionState(db.connect(p)).set_enabled("canvas", False)
    assert ConnectionState(db.connect(p)).enabled("canvas") is False   # persisted
    assert ConnectionState(db.connect(p)).enabled("google_calendar") is True
