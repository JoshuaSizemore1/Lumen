#!/usr/bin/env python3
"""Benchmark harness for Lumen's local inference layer.

Measures, per model/backend:
  - prefill latency (wall-clock TTFT) at ~200 / ~1500 / ~3000 prompt tokens
  - steady-state generation throughput (tok/s)
  - peak process RSS and peak system-wide memory (iGPU shares system RAM,
    so system-wide delta is the meaningful "GPU memory" number here)
  - wall-clock for a realistic Lumen tool call: system prompt + 13 MCP tool
    schemas, one invocation expected
  - tool-call correctness over 20 varied prompts

Backends:
  ollama    -> POST {url}/api/chat            (native, streaming)
  llamacpp  -> POST {url}/v1/chat/completions (OpenAI-compatible, streaming)

Research artifact. Does not import or modify Lumen source.

Usage:
  python bench/local_model_bench.py --backend ollama --model qwen3:4b-instruct \
      --url http://127.0.0.1:11434 --label qwen3-4b --out bench/results/

  python bench/local_model_bench.py --backend llamacpp --model bonsai-8b \
      --url http://127.0.0.1:8080 --label bonsai8b-nothink \
      --extra '{"chat_template_kwargs":{"enable_thinking":false}}' --out bench/results/
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lumen_tools import SYSTEM_PROMPT, TOOL_CALL_PROMPTS, TOOLS  # noqa: E402

# ---------------------------------------------------------------- filler text

_PARA = (
    "The quarterly planning review covered the migration timeline, the revised "
    "staffing estimate, and the open question of whether the vendor contract "
    "renews automatically in October. Dana raised the lease renegotiation again "
    "and asked for a written summary before the next sync. Chris circulated the "
    "invoice reconciliation spreadsheet with three line items still unmatched. "
)


def filler(target_tokens: int) -> str:
    """Rough filler; ~0.75 tokens/word. Actual count is read back from the API."""
    words_needed = int(target_tokens / 0.75)
    words = (_PARA * 200).split()
    return " ".join(words[:words_needed])


# ------------------------------------------------------------------ sampling


class MemSampler(threading.Thread):
    """Polls system-wide used memory and the RSS of a process tree."""

    def __init__(self, pids: list[int], interval: float = 0.10):
        super().__init__(daemon=True)
        self.pids = pids
        self.interval = interval
        self.stop_flag = threading.Event()
        self.peak_sys_used_kb = 0
        self.peak_rss_kb = 0
        self.baseline_sys_used_kb = self._sys_used_kb()

    @staticmethod
    def _sys_used_kb() -> int:
        vals = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                vals[k] = int(v.split()[0])
        return vals["MemTotal"] - vals["MemAvailable"]

    def _tree_rss_kb(self) -> int:
        total = 0
        for pid in list(self.pids):
            try:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            total += int(line.split()[1])
                            break
            except OSError:
                continue
        # include children (ollama spawns a runner subprocess)
        for pid in list(self.pids):
            try:
                kids = Path(f"/proc/{pid}/task").iterdir()
                for t in kids:
                    cf = t / "children"
                    if cf.exists():
                        for kid in cf.read_text().split():
                            if int(kid) not in self.pids:
                                self.pids.append(int(kid))
            except OSError:
                continue
        return total

    def run(self):
        while not self.stop_flag.is_set():
            self.peak_sys_used_kb = max(self.peak_sys_used_kb, self._sys_used_kb())
            self.peak_rss_kb = max(self.peak_rss_kb, self._tree_rss_kb())
            time.sleep(self.interval)

    def stop(self) -> dict:
        self.stop_flag.set()
        self.join(timeout=2)
        return {
            "peak_rss_mb": round(self.peak_rss_kb / 1024, 1),
            "peak_sys_used_mb": round(self.peak_sys_used_kb / 1024, 1),
            "sys_used_delta_mb": round(
                (self.peak_sys_used_kb - self.baseline_sys_used_kb) / 1024, 1
            ),
        }


def find_pids(patterns: list[str]) -> list[int]:
    pids = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().decode("utf8", "replace")
        except OSError:
            continue
        if any(pat in cmd for pat in patterns):
            pids.append(int(p.name))
    return pids


# ------------------------------------------------------------------ backends


class Backend:
    def __init__(self, url: str, model: str, extra: dict):
        self.url = url.rstrip("/")
        self.model = model
        self.extra = extra or {}

    def chat(self, messages, tools=None, max_tokens=256, timeout=900) -> dict:
        raise NotImplementedError


class OllamaBackend(Backend):
    name = "ollama"

    def chat(self, messages, tools=None, max_tokens=256, timeout=900):
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"num_predict": max_tokens, "temperature": 0},
        }
        if tools:
            body["tools"] = tools
        body.update(self.extra)

        t0 = time.perf_counter()
        ttft = None
        chunks, tool_calls, final = [], [], {}
        with requests.post(f"{self.url}/api/chat", json=body, stream=True,
                           timeout=timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                msg = d.get("message") or {}
                piece = msg.get("content") or ""
                tc = msg.get("tool_calls") or []
                if tc:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    tool_calls.extend(tc)
                if piece:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    chunks.append(piece)
                if d.get("done"):
                    final = d
        total = time.perf_counter() - t0
        n_out = final.get("eval_count") or 0
        return {
            "ttft_s": ttft,
            "total_s": total,
            "tokens_out": n_out,
            "prompt_tokens": final.get("prompt_eval_count"),
            "native_prefill_s": (final.get("prompt_eval_duration") or 0) / 1e9 or None,
            "native_decode_s": (final.get("eval_duration") or 0) / 1e9 or None,
            "text": "".join(chunks),
            "tool_calls": [
                {"name": c["function"]["name"], "arguments": c["function"]["arguments"]}
                for c in tool_calls if "function" in c
            ],
        }


class LlamaCppBackend(Backend):
    name = "llamacpp"

    def chat(self, messages, tools=None, max_tokens=256, timeout=900):
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        body.update(self.extra)

        t0 = time.perf_counter()
        ttft = None
        chunks, usage = [], {}
        tc_acc: dict[int, dict] = {}
        with requests.post(f"{self.url}/v1/chat/completions", json=body, stream=True,
                           timeout=timeout) as r:
            r.raise_for_status()
            for raw in r.iter_lines():
                if not raw:
                    continue
                line = raw.decode() if isinstance(raw, bytes) else raw
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                d = json.loads(payload)
                if d.get("usage"):
                    usage = d["usage"]
                for ch in d.get("choices", []):
                    delta = ch.get("delta") or {}
                    piece = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tc_acc.setdefault(idx, {"name": "", "arguments": ""})
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                    if piece or reasoning:
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        chunks.append(piece)
        total = time.perf_counter() - t0
        parsed = []
        for slot in tc_acc.values():
            args = slot["arguments"]
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                pass  # keep raw string; correctness check flags it
            parsed.append({"name": slot["name"], "arguments": args})
        return {
            "ttft_s": ttft,
            "total_s": total,
            "tokens_out": usage.get("completion_tokens") or 0,
            "prompt_tokens": usage.get("prompt_tokens"),
            "native_prefill_s": None,
            "native_decode_s": None,
            "text": "".join(chunks),
            "tool_calls": parsed,
        }


# -------------------------------------------------------------------- suites


def _nonce() -> str:
    """Unique high-entropy prefix. Both Ollama and llama-server do prefix KV
    caching; without a differing FIRST token every rep would measure a cache hit
    instead of prefill. Must lead the prompt, not trail it."""
    return " ".join(f"{os.urandom(2).hex()}" for _ in range(8))


def suite_prefill(be: Backend, sizes=(200, 1500, 3000), reps=3) -> list[dict]:
    out = []
    for size in sizes:
        def mk():
            return [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user",
                 "content": f"[ref {_nonce()}]\n" + filler(size)
                 + "\n\nReply with exactly one word: ok"},
            ]
        be.chat(mk(), max_tokens=1)  # warm the model, not the prompt cache
        runs = [be.chat(mk(), max_tokens=1) for _ in range(reps)]
        ttfts = [r["ttft_s"] for r in runs if r["ttft_s"] is not None]
        out.append({
            "target_tokens": size,
            "actual_prompt_tokens": runs[-1]["prompt_tokens"],
            "ttft_s": [round(t, 4) for t in ttfts],
            "ttft_median_s": round(statistics.median(ttfts), 4) if ttfts else None,
            "native_prefill_s": runs[-1]["native_prefill_s"],
        })
    return out


def suite_throughput(be: Backend, max_tokens=256, reps=3) -> dict:
    msgs = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content":
         "Write a detailed, continuous prose description of a coastal town in autumn. "
         "Do not use lists. Keep writing until you are told to stop."},
    ]
    be.chat(msgs, max_tokens=32)  # warm
    rates = []
    detail = []
    for _ in range(reps):
        r = be.chat(msgs, max_tokens=max_tokens)
        decode_s = r["total_s"] - (r["ttft_s"] or 0)
        n = r["tokens_out"]
        rate = (n - 1) / decode_s if decode_s > 0 and n > 1 else None
        if rate:
            rates.append(rate)
        detail.append({"tokens_out": n, "decode_s": round(decode_s, 3),
                       "tok_s": round(rate, 2) if rate else None})
    return {
        "runs": detail,
        "median_tok_s": round(statistics.median(rates), 2) if rates else None,
    }


def suite_realistic_tool_call(be: Backend, reps=5) -> dict:
    """Steady-state production shape: the system prompt + 13 tool schemas are a
    stable prefix (legitimately KV-cached across turns), the user turn is new
    each time. Varying the user message keeps that honest — repeating one
    message would measure a full-prompt cache hit."""
    users = [
        "What did Chris send me about the invoice?",
        "Did Dana email me about the lease yet?",
        "Any mail from Priya this week?",
        "Show me what Marcus sent about the contract.",
        "Find the message from Alina about scheduling.",
    ]
    be.chat([{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": "Anything from Sam?"}],
            tools=TOOLS, max_tokens=256)  # warm model + tool prefix
    runs = []
    for i in range(reps):
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": users[i % len(users)]},
        ]
        r = be.chat(msgs, tools=TOOLS, max_tokens=256)
        runs.append({
            "wall_s": round(r["total_s"], 3),
            "ttft_s": round(r["ttft_s"], 3) if r["ttft_s"] else None,
            "prompt_tokens": r["prompt_tokens"],
            "tokens_out": r["tokens_out"],
            "emitted_tool": bool(r["tool_calls"]),
            "tool": r["tool_calls"][0]["name"] if r["tool_calls"] else None,
        })
    walls = [x["wall_s"] for x in runs]
    return {
        "runs": runs,
        "median_wall_s": round(statistics.median(walls), 3),
        "min_wall_s": round(min(walls), 3),
        "max_wall_s": round(max(walls), 3),
    }


def suite_tool_correctness(be: Backend) -> dict:
    results = []
    ok = valid_json = right_tool = right_args = 0
    for prompt, expected, pred in TOOL_CALL_PROMPTS:
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            r = be.chat(msgs, tools=TOOLS, max_tokens=256)
        except Exception as e:  # noqa: BLE001
            results.append({"prompt": prompt, "error": str(e)[:200]})
            continue
        tcs = r["tool_calls"]
        got = tcs[0] if tcs else None
        args = got["arguments"] if got else None
        args_is_dict = isinstance(args, dict)
        t_ok = bool(got) and got["name"] == expected
        a_ok = bool(t_ok and args_is_dict and (pred is None or _safe(pred, args)))
        valid_json += 1 if args_is_dict else 0
        right_tool += 1 if t_ok else 0
        right_args += 1 if a_ok else 0
        ok += 1 if a_ok else 0
        results.append({
            "prompt": prompt,
            "expected": expected,
            "got": got["name"] if got else None,
            "args": args if args_is_dict else repr(args)[:200],
            "valid_json_args": args_is_dict,
            "correct_tool": t_ok,
            "correct_args": a_ok,
            "wall_s": round(r["total_s"], 3),
        })
    n = len(TOOL_CALL_PROMPTS)
    return {
        "n": n,
        "emitted_valid_json_args": valid_json,
        "correct_tool": right_tool,
        "correct_tool_and_args": right_args,
        "correctness_rate": round(ok / n, 3),
        "detail": results,
    }


def _safe(pred, args):
    try:
        return bool(pred(args))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["ollama", "llamacpp"], required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", default="bench/results")
    ap.add_argument("--extra", default="{}", help="JSON merged into request body")
    ap.add_argument("--pid-match", default="", help="comma-separated cmdline substrings")
    ap.add_argument("--suites", default="prefill,throughput,realistic,correctness")
    ap.add_argument("--gen-tokens", type=int, default=256)
    args = ap.parse_args()

    extra = json.loads(args.extra)
    cls = OllamaBackend if args.backend == "ollama" else LlamaCppBackend
    be = cls(args.url, args.model, extra)

    pats = [p for p in args.pid_match.split(",") if p] or (
        ["ollama"] if args.backend == "ollama" else ["llama-server"])
    pids = find_pids(pats)
    sampler = MemSampler(pids)
    sampler.start()

    suites = set(args.suites.split(","))
    report = {
        "label": args.label,
        "backend": args.backend,
        "model": args.model,
        "url": args.url,
        "extra": extra,
        "server_pids": pids,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    t_all = time.perf_counter()
    if "prefill" in suites:
        print(f"[{args.label}] prefill...", flush=True)
        report["prefill"] = suite_prefill(be)
    if "throughput" in suites:
        print(f"[{args.label}] throughput...", flush=True)
        report["throughput"] = suite_throughput(be, max_tokens=args.gen_tokens)
    if "realistic" in suites:
        print(f"[{args.label}] realistic tool call...", flush=True)
        report["realistic_tool_call"] = suite_realistic_tool_call(be)
    if "correctness" in suites:
        print(f"[{args.label}] tool correctness (20 prompts)...", flush=True)
        report["tool_correctness"] = suite_tool_correctness(be)
    report["total_bench_s"] = round(time.perf_counter() - t_all, 1)
    report["memory"] = sampler.stop()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / f"{args.label}.json"
    dest.write_text(json.dumps(report, indent=2))
    print(f"[{args.label}] wrote {dest}")
    print(json.dumps({k: v for k, v in report.items()
                      if k in ("memory", "total_bench_s")}, indent=2))


if __name__ == "__main__":
    main()
