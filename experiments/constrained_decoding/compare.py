#!/usr/bin/env python3
"""Does constrained decoding remove the need for retry/repair logic?

Three arms over the same 30 prompts against the same server:

  A  unconstrained   free-text, system prompt asks for a bare JSON object.
                     This is the arm that measures how bad the problem is.
  B  native          OpenAI-style `tools` + `tool_choice` (llama.cpp --jinja).
  C  json_schema     `response_format: {type: json_schema, ...}` — llama.cpp
                     compiles the schema to GBNF and masks logits during decode.

Malformation is judged identically for all three, on the *parsed* result:
  - could not produce a JSON object at all
  - `tool` missing / not one of the known tools
  - `arguments` missing or not an object

Argument *semantics* (right tool, sane params) is scored separately — a
grammar can guarantee shape, never correctness. Keeping the two apart is the
whole point of the experiment.

Research artifact. Does not import or modify Lumen source.

  python experiments/constrained_decoding/compare.py \
      --url http://127.0.0.1:8080 --model bonsai-8b --label bonsai8b
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))
from lumen_tools import SYSTEM_PROMPT, TOOL_CALL_PROMPTS, TOOLS  # noqa: E402

TOOL_NAMES = [t["function"]["name"] for t in TOOLS]

# 20 shared with the bench + 10 harder ones that tempt prose or ambiguity.
EXTRA_PROMPTS = [
    ("Is there anything I should deal with today?", "search_email", None),
    ("I think Priya emailed about the budget — can you find it?", "search_email",
     lambda a: "priya" in str(a.get("query", "")).lower()),
    ("Delete todo 7, I already did it.", "complete_todo", None),
    ("When's my next dentist appointment?", "list_events", None),
    ("Put 'file taxes' on the list for April 15.", "add_todo",
     lambda a: "tax" in str(a.get("text", "")).lower()),
    ("Which books do I have on Roman history?", "search_books", None),
    ("Open /home/josh/notes/2026-07-17.md", "read_file",
     lambda a: a.get("path") == "/home/josh/notes/2026-07-17.md"),
    ("Anything unread from this week that mentions the contract?", "search_email", None),
    ("Show me next month's schedule.", "list_events", None),
    ("What's the id of the last email Chris sent?", "search_email",
     lambda a: "chris" in str(a.get("query", "")).lower()),
]

PROMPTS = list(TOOL_CALL_PROMPTS) + EXTRA_PROMPTS

# The naive schema: constrains the envelope, leaves `arguments` a free-form
# object. This is what most people reach for first, and it is why the
# json_schema arm initially scored no better than unconstrained — the model
# emitted perfect JSON with invented parameter names (`message_id` for `id`,
# `start_date` for `start`).
CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": TOOL_NAMES},
        "arguments": {"type": "object"},
    },
    "required": ["tool", "arguments"],
    "additionalProperties": False,
}


def _strict_schema():
    """Discriminated union over the 13 real tools, each with its own parameter
    schema and `additionalProperties: false`. This is what a json_schema arm has
    to look like to actually constrain parameter names."""
    variants = []
    for t in TOOLS:
        fn = t["function"]
        params = fn["parameters"]
        variants.append({
            "type": "object",
            "properties": {
                "tool": {"const": fn["name"]},
                "arguments": {
                    "type": "object",
                    "properties": params.get("properties", {}),
                    "required": params.get("required", []),
                    "additionalProperties": False,
                },
            },
            "required": ["tool", "arguments"],
            "additionalProperties": False,
        })
    return {"anyOf": variants}


CALL_SCHEMA_STRICT = _strict_schema()

JSON_INSTRUCTION = (
    "\n\nRespond with ONLY a single JSON object, no prose, no markdown fence, in "
    'exactly this form: {"tool": "<one of: '
    + ", ".join(TOOL_NAMES)
    + '>", "arguments": {...}}'
)

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str):
    """Best-effort salvage — mirrors what repair logic would have to do.
    Returns (obj|None, needed_salvage)."""
    if not text:
        return None, False
    try:
        return json.loads(text.strip()), False
    except json.JSONDecodeError:
        pass
    m = FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip()), True
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1]), True
        except json.JSONDecodeError:
            pass
    return None, True


def judge(obj):
    """Shape-only verdict, identical across arms."""
    if not isinstance(obj, dict):
        return False, "not-an-object"
    if "tool" not in obj:
        return False, "missing-tool"
    if obj["tool"] not in TOOL_NAMES:
        return False, f"unknown-tool:{obj['tool']}"
    if "arguments" not in obj:
        return False, "missing-arguments"
    if not isinstance(obj["arguments"], dict):
        return False, "arguments-not-object"
    return True, "ok"


def post(url, body, timeout=600):
    t0 = time.perf_counter()
    r = requests.post(f"{url}/v1/chat/completions", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json(), time.perf_counter() - t0


JSON_ARMS = ("unconstrained", "json_schema", "json_schema_strict")


def run_arm(url, model, arm, prompt, expected, pred, max_tokens=256):
    sys_p = SYSTEM_PROMPT + (JSON_INSTRUCTION if arm in JSON_ARMS else "")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": sys_p},
                     {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if arm == "native":
        body["tools"] = TOOLS
        body["tool_choice"] = "auto"
    elif arm in ("json_schema", "json_schema_strict"):
        schema = CALL_SCHEMA if arm == "json_schema" else CALL_SCHEMA_STRICT
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "tool_call", "strict": True, "schema": schema},
        }

    d, wall = post(url, body)
    msg = d["choices"][0]["message"]
    salvaged = False

    if arm == "native":
        tcs = msg.get("tool_calls") or []
        if tcs:
            fn = tcs[0]["function"]
            try:
                args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) \
                    else fn["arguments"]
            except json.JSONDecodeError:
                args = None
            obj = {"tool": fn["name"], "arguments": args}
        else:
            obj, salvaged = extract_json(msg.get("content") or "")
    else:
        obj, salvaged = extract_json(msg.get("content") or "")

    well_formed, reason = judge(obj)
    right_tool = bool(well_formed and obj["tool"] == expected)
    right_args = bool(right_tool and (pred is None or _safe(pred, obj["arguments"])))
    return {
        "prompt": prompt,
        "arm": arm,
        "wall_s": round(wall, 3),
        "well_formed": well_formed,
        "reason": reason,
        "needed_salvage": salvaged,
        "expected": expected,
        "got_tool": obj.get("tool") if isinstance(obj, dict) else None,
        "args": obj.get("arguments") if isinstance(obj, dict) else None,
        "correct_tool": right_tool,
        "correct_args": right_args,
        "completion_tokens": (d.get("usage") or {}).get("completion_tokens"),
        "raw": (msg.get("content") or "")[:300],
    }


def _safe(pred, args):
    try:
        return bool(pred(args))
    except Exception:  # noqa: BLE001
        return False


def summarize(rows):
    n = len(rows)
    if not n:
        return {}
    walls = [r["wall_s"] for r in rows]
    return {
        "n": n,
        "well_formed": sum(r["well_formed"] for r in rows),
        "malformation_rate": round(1 - sum(r["well_formed"] for r in rows) / n, 3),
        "needed_salvage": sum(r["needed_salvage"] for r in rows),
        "strict_malformation_rate": round(
            1 - sum(r["well_formed"] and not r["needed_salvage"] for r in rows) / n, 3),
        "correct_tool": sum(r["correct_tool"] for r in rows),
        "correct_args": sum(r["correct_args"] for r in rows),
        "semantic_accuracy": round(sum(r["correct_args"] for r in rows) / n, 3),
        "median_wall_s": round(statistics.median(walls), 3),
        "mean_wall_s": round(statistics.mean(walls), 3),
        "median_completion_tokens": statistics.median(
            [r["completion_tokens"] or 0 for r in rows]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--arms",
                    default="unconstrained,native,json_schema,json_schema_strict")
    ap.add_argument("--out", default="experiments/constrained_decoding/results")
    args = ap.parse_args()

    report = {"label": args.label, "model": args.model, "url": args.url,
              "n_prompts": len(PROMPTS), "arms": {}}

    for arm in args.arms.split(","):
        print(f"[{args.label}] arm={arm} ({len(PROMPTS)} prompts)...", flush=True)
        rows = []
        for prompt, expected, pred in PROMPTS:
            try:
                rows.append(run_arm(args.url, args.model, arm, prompt, expected, pred))
            except Exception as e:  # noqa: BLE001
                rows.append({"prompt": prompt, "arm": arm, "error": str(e)[:200],
                             "well_formed": False, "reason": "request-error",
                             "needed_salvage": False, "correct_tool": False,
                             "correct_args": False, "wall_s": 0.0,
                             "completion_tokens": 0})
        report["arms"][arm] = {"summary": summarize(rows), "detail": rows}
        s = report["arms"][arm]["summary"]
        print(f"    malformation={s['malformation_rate']} "
              f"strict={s['strict_malformation_rate']} "
              f"semantic={s['semantic_accuracy']} median={s['median_wall_s']}s", flush=True)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / f"{args.label}.json"
    dest.write_text(json.dumps(report, indent=2))
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
