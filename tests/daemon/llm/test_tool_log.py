import json
from datetime import datetime

from lumen.daemon.llm.tool_log import ToolLog


def test_write_appends_jsonl_line(tmp_path):
    p = tmp_path / "sub" / "tool-calls.jsonl"
    log = ToolLog(p)
    log.write("list_directory", {"path": "/n"}, True, "a.txt\nb.txt", 12)
    log.write("read_file", {"path": "/n/a.txt"}, True, "hello", 5)
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool"] == "list_directory"
    assert first["arguments"] == {"path": "/n"}
    assert first["ok"] is True
    assert first["result_excerpt"] == "a.txt\nb.txt"
    assert first["duration_ms"] == 12
    assert "ts" in first
    assert datetime.fromisoformat(first["ts"]).tzinfo is not None


def test_write_truncates_long_results(tmp_path):
    p = tmp_path / "tool-calls.jsonl"
    ToolLog(p, excerpt_len=10).write("x", {}, True, "y" * 5000, 1)
    rec = json.loads(p.read_text().splitlines()[0])
    assert len(rec["result_excerpt"]) == 10


def test_write_records_errors(tmp_path):
    p = tmp_path / "tool-calls.jsonl"
    ToolLog(p).write("read_file", {"path": "/x"}, False, "tool error: not found", 3)
    rec = json.loads(p.read_text().splitlines()[0])
    assert rec["ok"] is False
    assert "not found" in rec["result_excerpt"]
