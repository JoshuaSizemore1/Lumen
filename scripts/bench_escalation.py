"""Benchmark a candidate escalation model on this hardware before wiring it
into ModelRouter (llm-serving.md rule: never commit to a size unmeasured).

Measures, per run:
  1. Cold start: force-unload, then time to first token of a trivial prompt.
  2. Tool-call fitness: three write-shaped prompts against a stub write_file
     schema — the model must emit a well-formed tool_call (name + path arg).
  3. Thermals: hottest thermal zone before and right after the work.

Usage: uv run python scripts/bench_escalation.py [model] (default qwen3:14b)
"""

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:11434"
KEEP_ALIVE = "10m"   # same policy as the daemon; unload is forced explicitly below

WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
}

TOOL_PROMPTS = [
    "Save a file /tmp/lumen-bench/hello.txt containing exactly: hello world",
    "Write the text 'meeting notes for Friday' to /tmp/lumen-bench/notes.txt",
    "Create /tmp/lumen-bench/todo.md with a one-line markdown todo list",
]


def max_zone_temp() -> float | None:
    temps = []
    for zone in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            temps.append(int(zone.read_text().strip()) / 1000)
        except (OSError, ValueError):
            continue
    return max(temps) if temps else None


def unload(client: httpx.Client, model: str) -> None:
    client.post(f"{BASE}/api/chat",
                json={"model": model, "messages": [], "keep_alive": 0})
    time.sleep(3)   # give Ollama a moment to actually evict


def cold_start_first_token(client: httpx.Client, model: str) -> float:
    body = {"model": model, "stream": True, "keep_alive": KEEP_ALIVE,
            "think": False,
            "messages": [{"role": "user", "content": "Say only: ready"}]}
    start = time.monotonic()
    with client.stream("POST", f"{BASE}/api/chat", json=body) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("message", {}).get("content"):
                return time.monotonic() - start
            if data.get("done"):
                break
    raise RuntimeError("stream ended without content")


def tool_call_check(client: httpx.Client, model: str, prompt: str) -> tuple[bool, float, str]:
    body = {"model": model, "stream": False, "keep_alive": KEEP_ALIVE,
            "think": False, "tools": [WRITE_FILE_TOOL],
            "messages": [{"role": "user", "content": prompt}]}
    start = time.monotonic()
    resp = client.post(f"{BASE}/api/chat", json=body)
    resp.raise_for_status()
    took = time.monotonic() - start
    calls = resp.json().get("message", {}).get("tool_calls") or []
    for call in calls:
        fn = call.get("function", {})
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if fn.get("name") == "write_file" and args.get("path") and "content" in args:
            return True, took, f"path={args['path']!r}"
    return False, took, f"no well-formed call (got {len(calls)} tool_calls)"


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3:14b"
    client = httpx.Client(timeout=httpx.Timeout(connect=5, read=None, write=10, pool=5))

    print(f"== escalation benchmark: {model} ==")
    t0 = max_zone_temp()
    print(f"thermal max before: {t0:.0f}°C" if t0 else "thermal zones unreadable")

    print("forcing unload…")
    unload(client, model)
    print("cold start (unloaded → first token)…")
    cold = cold_start_first_token(client, model)
    print(f"  cold start: {cold:.1f}s")

    warm = cold_start_first_token(client, model)
    print(f"  warm first token: {warm:.1f}s")

    ok_count = 0
    for prompt in TOOL_PROMPTS:
        ok, took, detail = tool_call_check(client, model, prompt)
        ok_count += ok
        print(f"  tool call {'OK ' if ok else 'BAD'} {took:5.1f}s  {detail}")

    t1 = max_zone_temp()
    if t0 and t1:
        print(f"thermal max after: {t1:.0f}°C (delta {t1 - t0:+.0f})")

    print("unloading (leave the machine as found)…")
    unload(client, model)
    print(f"== result: cold {cold:.1f}s, warm {warm:.1f}s, "
          f"tool calls {ok_count}/{len(TOOL_PROMPTS)} ==")


if __name__ == "__main__":
    main()
