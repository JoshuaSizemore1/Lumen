# Local inference evaluation — Ternary Bonsai and constrained decoding

Date: 2026-07-18 · All numbers measured on this machine · Research only, no Lumen source changed

---

## Correction — 2026-07-18, same day

**The refusal figures below do not describe production.** They were measured against
`bench/lumen_tools.py`, a hand-written *copy* of Lumen's tool surface that had drifted from the
real one in three ways:

- It graded four todo tools (`list_todos` / `add_todo` / `complete_todo` / `delete_todo`) that do
  not exist. Todos are a context-only pseudo-group; NL add/mark-done are the `TODO_ADD` /
  `MARK_DONE` regex routes in `router.py`, which fire before the model is ever called.
- It graded a `lookup_book` that does not exist, and described `search_books` as searching "the
  user's book catalog". The real tool searches the **public Open Library**; the user's own books
  are a reading log injected as context.
- Its system prompt was not `IDENTITY`.

So "13.3% refusal on qwen3" and the 20-prompt correctness numbers describe a prompt Lumen never
ships. The malformation, latency, memory, and constrained-decoding measurements are unaffected —
those compare arms against each other, and the divergence is common to all of them.

**Re-measured against the real surface** (real `IDENTITY`, real MCP descriptions read off the live
FastMCP servers, real `calendar_context` / `mail_context`), on the seven private-topic probes now
in `tests/eval/cases.py`:

| Prompt text | Calendar probes refused | Mail probes refused |
|---|---|---|
| As shipped before this work | **4 / 4** | 0 / 3 |
| After the `list_events` + `calendar_context` rewrite | 3 / 4 | 0 / 3 |
| After additionally stating the search affordance | **~0 / 4** | 0 / 3 |

The last row is a rate, not a certainty: over ~70 post-fix probe executions the residual refusal
rate is ~3–4%, all of it on one probe ("when is the parent-teacher conference?"). That probe
refused 100% of the time before the fix.

Two things that matters for:

1. **Open question 1 is answered — it was a schema-description problem** (see that section).
2. **The refusal was calendar-only.** `search_email` was never refused, before or after. The
   asymmetry was in the wording, not the model or the topic.

The eval now lives in `tests/eval/` and derives its tool schemas from the servers themselves, so
this class of divergence cannot recur. `bench/` is kept as the historical artifact that produced
the numbers below.

---

## Recommendation

1. **Bonsai 8B — do not adopt.** It passes every stated criterion against the *stated* baseline,
   but a control run shows the win was the **server, not the model**: qwen3:4b on llama.cpp beats
   Bonsai 8B on every speed axis.
2. ~~**Instead: move Ollama → llama.cpp, keep qwen3:4b.** Realistic tool call **4.113 s → 2.023 s
   (2.03×)**~~ — **the ratio did not reproduce, and the migration was reverted 2026-07-19.**
   Re-measured against the real production prompt and the real (smaller) tool surface, warmed,
   both backends alternating: **1.53× at 2 schemas, 1.29× at 5, 1.23× at 16** — a roughly fixed
   0.5–0.7 s saving rather than a multiplier, shrinking as the request gets heavier.

   The reason the "win" is small is the reason it should never have been framed as a migration:
   **Ollama runs llama.cpp under the hood** (`/usr/lib/ollama/llama-server`). Both arms were the
   same inference engine on the same weights; the delta was Ollama's wrapper overhead, not a
   faster runtime. Measuring "llama.cpp vs Ollama" as if they were competing engines was a
   category error in the original brief that the numbers inherited.

   The backend was built, verified (18/19 on the corrected eval, idle-unload armed via
   `--sleep-idle-seconds`), and then **deleted** — `.claude/skills/llm-serving.md` already said
   *"Don't run a second model server 'just in case' — one Ollama instance, one idle-unload
   policy,"* and a fixed 0.5 s does not buy an exemption from that rule. What it cost was a
   process supervisor, a restart policy, a second idle-unload mechanism, a hardcoded GGUF blob
   path that silently rots when the model is re-pulled, and embeddings permanently split across
   two servers. Ollama's own overhead is the thing to attack if this matters later.
3. **Bonsai 27B — rejected outright.** Crashes the Vulkan device (`ErrorDeviceLost`, core dumped).
4. **Guidance — do not adopt.** Its integration path is empirically broken here, and it is
   unnecessary.
5. ~~**Constrained decoding: land on tier 2**~~ — **revised, see Correction.** The 13.3% that
   ruled out tier 1 was a docstring defect, now fixed in the prompt for free. **Do not constrain
   the tool loop**: on tool dispatch specifically, tier 2 measured *worse* than native (0.733 vs
   0.767), losing 4 cases to win 3, and it converts refusals into confident wrong calls. Keep
   tier 2 on the JSON-emitting sites (`event_create`, triage) where there is no refusal mode.

---

## Contradictions with the brief

Every item below was asserted in the brief and is wrong, stale, or backwards.

| # | Brief said | Measured |
|---|---|---|
| 1 | ASUS Zenbook Duo 2025, Lunar Lake, **Core Ultra 200V**, **Arc 140V**, LPDDR5X-8533 ~136 GB/s | **Core Ultra 9 285H** (Arrow Lake-H), **Arc Pro 130T/140T** (`8086:7d51`), Mesa. Different iGPU generation *and* memory subsystem. Every bandwidth-derived expectation in the brief is anchored to the wrong machine. |
| 2 | Vulkan gets the **generic path**; optimized kernels are CUDA + Metal | **False.** Real Vulkan Q2_0 shaders (`dequant_q2_0.comp`) landed in PrismML PR #32 on 2026-05-19. Q2_0 ran fully on Vulkan with **zero** fallback/unsupported warnings across every run. |
| 3 | (implicit) the HF model card is authoritative | `Ternary-Bonsai-8B-gguf` README still says the fork adds Q2_0 for "**CPU (NEON/generic) and Metal**" — omits Vulkan and CUDA. **Stale by two months.** |
| 4 | — | Upstream llama.cpp merged Q2_0 + Vulkan (ggml-org #25430, 2026-07-17) at **`QK2_0=64`**. Bonsai ships **g128**. Mainline llama.cpp and Ollama **still cannot load Bonsai GGUFs**. Anyone reading "upstream landed Q2_0" walks into this. |
| 5 | `BONSAI_THINKING=0`, `BONSAI_KV4=1` | Not in the fork — `grep BONSAI_` returns nothing. They belong to the Bonsai-demo wrapper. Server-level equivalents: `--reasoning-budget 0`, `-ctk q4_0 -ctv q4_0`. |
| 6 | Bonsai **reasons by default** | **False on this path.** The template defaults to non-thinking; `--reasoning-budget 0` was a no-op. See [Thinking](#thinking-mode-was-never-on-by-default). |
| 7 | "30 GiB should hold" the 27B | Memory was never the constraint — 26.9 GiB free at load. The **GPU died** instead. |
| 8 | Assumes Lumen has **retry/repair logic** to remove | Lumen has **no retry loops**. It has hand-rolled JSON *salvage* in 6 modules, and malformation causes **silent drops**, not retries. |
| 9 | — | Fork HEAD (`9fcaed7`, dated today) adds **AVX512-VNNI** Q2_0 CPU kernels. Arrow Lake-H has **no AVX-512**, so that path never engages here. |
| 10 | DSpark speculative decoding is CUDA-only | Confirmed. `BONSAI_SPECULATIVE=1`, 27B + dspark drafter, CUDA. Unusable here. |

---

## Environment

| Item | Value |
|---|---|
| CPU | Intel Core Ultra 9 285H, 16C/16T, no SMT |
| ISA | `avx avx2 avx_vnni f16c fma` — **no AVX-512** |
| GPU | Intel Arc Pro 130T/140T iGPU (`8086:7d51`), Mesa, Vulkan 1.4.354, 23 586 MiB shared |
| Vulkan caps | `uma:1 fp16:1 bf16:1 warp:32 shared:49152 int_dot:1 ` **`matrix cores: none`** |
| RAM | 30 GiB |
| Ollama | 0.32.1 (`ollama-vulkan`), baseline confirmed **100% GPU**, 32 768 ctx |
| llama.cpp | `PrismML-Eng/llama.cpp` @ `9fcaed7` (prism), Vulkan |

No XMX on this iGPU — no matrix-engine acceleration for either model.

---

## Setup deviations on Arc + Vulkan

Bonsai-demo ships `build_mac.sh`, `build_cuda_linux.sh`, `build_cuda_windows.ps1` — **no
Linux+Vulkan script**. Arc + Vulkan is genuinely untested upstream. I built `llama-server` from
the fork and drove its OpenAI-compatible API directly.

| # | Failure | Fix |
|---|---|---|
| 1 | `vulkan.h` missing. Arch ships `vulkan-icd-loader` + `vulkan-intel` but **not `vulkan-headers`** — yet `pkg-config --modversion vulkan` still reports 1.4.350, so headers *look* present | No root → vendored `Vulkan-Headers` v1.4.350; `-DVulkan_INCLUDE_DIR=…` |
| 2 | `find_package(SPIRV-Headers)` fails (`spirv-tools` installed, `spirv-headers` not) | Installed SPIRV-Headers to a local prefix; `-DCMAKE_PREFIX_PATH=…` |
| 3 | Configures, then compile dies: `fatal error: spirv/unified1/spirv.hpp`. `find_package` succeeds **without propagating the include dir** to the `ggml-vulkan` target | `-DCMAKE_CXX_FLAGS=-I$PREFIX/include` |
| 4 | `llama-server` defaults to **4 parallel slots**, each allocating full `n_ctx` KV → ~4× memory vs single-user reality | `-np 1` |
| 5 | `pkill -f "llama-server.*Bonsai-8B"` matched **its own command line** and killed the harness | `for p in $(pgrep -x llama-server); do kill $p; done` |

Build was otherwise clean: no Q2_0 warnings, no kernel-fallback messages.

---

## Methodology corrections

Two measurement bugs, both of which *flattered* the baseline. Stating them because the corrected
numbers are 40–70× different.

1. **The first baseline run measured KV-cache hits, not prefill.** Warmup reused the identical
   prompt, so every timed rep hit the prefix cache — Ollama reported `prompt_eval_duration` of
   **0.096 s for a 4 555-token prompt** (~47 000 tok/s, impossible). Fixed with a unique
   high-entropy nonce leading each request. **Any benchmark that does not defeat prefix caching
   is measuring nothing.**
2. **Realistic tool call: the cache is legitimate, the repetition was not.** In production the
   system prompt + 13 tool schemas are a stable prefix that *should* cache; the user turn is new.
   The suite now varies the user turn and holds the prefix fixed.
3. **Ollama does not stream partial `tool_calls`** — they arrive in one chunk at the end, so
   TTFT ≡ total there, while llama.cpp streams them. **Compare wall-clock, not TTFT**, across the
   two servers.
4. **Ollama parses tool calls server-side**, so a malformed call is dropped and is
   indistinguishable from "no call". Its `valid_json` column means "emitted a call at all".

Harness: [bench/local_model_bench.py](../../bench/local_model_bench.py) ·
schemas [bench/lumen_tools.py](../../bench/lumen_tools.py) (13 tools, matching the count recorded
in [lumen/config.toml](../../lumen/config.toml)) · drivers `bench/run_matrix.sh`,
`bench/run_followup.sh`.

---

## Investigation A — results

### The control run is the whole story

`qwen3-4b Ollama` is the current production path. `qwen3-4b llama.cpp` is the same model,
same weights (Ollama's own GGUF blob), different server — the control the brief did not ask for.

| metric | qwen3:4b **Ollama** | qwen3:4b **llama.cpp** | Bonsai 8B **llama.cpp** |
|---|---|---|---|
| prefill TTFT @ ~365 tok | 2.1109 s | **1.7246 s** | 5.3961 s |
| prefill TTFT @ ~2 330 tok | 14.5848 s | **14.3733 s** | 40.5486 s |
| prefill TTFT @ ~4 595 tok | **38.7743 s** | 40.5997 s | 84.4059 s |
| decode | 10.44 tok/s | **16.79 tok/s** | 14.70 tok/s |
| **realistic tool call (median)** | 4.113 s | **2.023 s** | 2.891 s |
| realistic TTFT | 4.058 s (≡ wall) | **0.950 s** | 1.347 s |
| tool correctness | 0.85 | 0.85 | **0.95** |
| valid JSON args | 17/20 | 17/20 | **20/20** |
| peak model-attributable RAM | — | 7 612 MiB | **3 840 MiB** |
| free RAM at peak | — | 19 159 MiB | **23 107 MiB** |

Raw prefill samples, Bonsai @4 597: `101.219 / 84.406 / 83.825` s.
Realistic tool call, all 5 reps — Ollama `4.058 3.565 4.418 4.155 4.113`; llama.cpp qwen3
`2.023` median; Bonsai `2.973 2.827 2.891 2.883 3.011`.

**Reading it:**

- Switching Ollama → llama.cpp with the *same model* takes the realistic tool call from
  4.113 s to 2.023 s and decode from 10.44 to 16.79 tok/s. **That is a 2.03× win for free.**
- Bonsai 8B's apparent 4.113 → 2.891 s advantage was the server. Against the correct control it
  is **43% slower** (2.891 vs 2.023) and **2.8× slower at prefill** (40.5 vs 14.4 s @2 330).
- Bonsai's real, durable advantages are **correctness (+0.10)** and **memory (half)**. Neither
  justifies a forked runtime.
- Correctness is a *model* property, not a server one: qwen3 scored 0.85 on both servers, with
  the identical 3 failures.

### The migration's one hard prerequisite — checked, and it holds

Switching servers only works if `llama-server` can honour the non-negotiable idle-unload rule
(`idle_unload_minutes`, default 10) that Ollama currently enforces. It can:
**`--sleep-idle-seconds N`** (default `-1` = disabled). Measured with qwen3:4b at `-c 8192`:

| Phase | MemAvailable | Δ |
|---|---|---|
| before load | 26 795 MiB | — |
| loaded + active | 22 723 MiB | model = 4 072 MiB |
| after 20 s idle → sleeping | 26 449 MiB | **released 3 726 MiB (91.5%)** |

Server log confirms `handle_sleep: server is entering sleeping state`.

| Call | Latency |
|---|---|
| warm | 0.587 s |
| **cold, waking from sleep** | **1.660 s** |
| warm again | 0.504 s |

**Wake costs ~1.1 s**, and memory returns on wake (26 437 → 22 753 MiB). So the power/thermal
constraint is satisfiable, and the wake penalty is smaller than Ollama's model reload. This was
the one thing that could have invalidated the primary recommendation; it does not.

### Decision criteria, scored honestly

Against the **stated** baseline (qwen3:4b on Ollama), Bonsai 8B **passes all four**:

| Criterion | Result | |
|---|---|---|
| correctness ≥ baseline | 0.95 ≥ 0.85 | ✅ |
| realistic tool call within 2× | 2.891 s vs 4.113 s — *faster* | ✅ |
| peak memory leaves ≥ 18 GiB free | 23 107 MiB free | ✅ |
| no crashes or fallback warnings | 0 warnings, no crash over ~13 min | ✅ |

**And the recommendation is still "do not adopt."** The criteria were written against a baseline
that turns out to be a slow server, not a slow model. Passing them is an artifact of that
choice. Judged against qwen3:4b on the same server, Bonsai loses the latency case and wins only
on correctness and RAM — neither worth tracking a fork whose HEAD moved *today* and whose g128
format mainline cannot load.

### Bonsai 27B — crashes the GPU

```
terminate called after throwing an instance of 'vk::DeviceLostError'
  what():  vk::Device::waitForFences: ErrorDeviceLost
run_matrix.sh: line 20: 21534 Aborted (core dumped)
```

| Stage | Outcome |
|---|---|
| Load — 6.7 GiB weights, `n_ctx` 32 768 (`n_ctx_train` 262 144) | OK |
| Prefill 369 tokens | OK — 31.170 s, **11.84 tok/s** |
| Prefill 2 337 tokens | **Vulkan device loss, aborted, core dumped** |

No result file. GPU recovered afterwards (re-enumerated, 21 228 MiB free) — driver-level device
loss, not permanent damage.

Two consequences: (a) **memory was never the binding constraint** — the brief's "30 GiB should
hold it" tests the wrong resource; (b) this **corroborates the existing qwen3:14b note in
`config.toml`** (14B crashes the Vulkan runner on ~5.6k-token prompts). Same failure class, one
tier lower. The pattern on this Arc + Mesa stack is *large model × long prefill → device loss*,
now seen at 14B and 27B. Bonsai 8B showed none of it.

### Thinking mode was never on by default

First attempt (omitting `--reasoning-budget 0`) produced results identical to thinking-off:

| metric | "OFF" | "ON" (default budget) | genuinely on (`-rea on`) |
|---|---|---|---|
| realistic tool call | 2.891 s | 2.893 s | 2.925 s |
| output tokens (tool call) | 28–30 | 28–30 | 28–30 |
| correctness | 0.95 | 0.95 | 0.95 |
| decode | 14.70 tok/s | 14.67 tok/s | — |

Identical within noise, with identical output lengths. The template's default is **non-thinking**,
so `--reasoning-budget 0` was a no-op.

With `-rea on` the model *does* reason on open questions — an arithmetic prompt produced 359
completion tokens opening `"Let's think through this step by step."` — but **`reasoning_content`
was empty**: it reasons in plain `content`, so llama.cpp's reasoning parser extracts nothing.

**Cost of thinking on tool dispatch: ~zero** (2.925 vs 2.891 s, same output length). Reasoning
only engages on prompts that invite it. That makes the brief's framing — disable thinking to
save latency — moot for the tool path.

### Thermals

| Run | peak fan | peak pkg temp |
|---|---|---|
| idle | 0 RPM | 43 °C |
| Bonsai 8B nothink | 3 993 RPM | 70 °C |
| Bonsai 8B think | 3 941 RPM | 73 °C |
| Bonsai 27B (until crash) | 3 927 RPM | 73 °C |

Fans go 0 → ~3 990 RPM within the first minute of sustained generation and **stay there** —
clearly audible in a quiet room. This is a bandwidth-bound workload on a UMA part, so the ramp
tracks the whole run rather than spiking. All three configs land in the same envelope, so fan
noise does not discriminate between models.

*Gap: telemetry was sampled only during the matrix runs; the qwen3 configs were not sampled.
Desktop responsiveness was not measured under a controlled protocol — see open questions.*

---

## Investigation B — constrained decoding

### Mechanism

Guidance ≥ 0.2.0 delegates its grammar engine to **llguidance** (Rust).

| Stage | What happens |
|---|---|
| Per token | Computes a **token mask** — every token that can still lead to a grammar-accepted string |
| Application | Mask applied to logits **before sampling**; disallowed tokens → `-inf`. It constrains *sampling*; it does not post-validate |
| Cost | ~50 µs CPU/token at a 128k tokenizer; negligible startup |
| Fast-forward | Where the grammar makes continuation deterministic, tokens are **inserted without a forward pass** — a real speedup, not just correctness |
| Token healing | Repairs tokenization artifacts at the prompt/generation boundary. **Requires direct endpoint integration → `LlamaCpp`/`Transformers` only** |

**Beyond JSON schema:** regex, Lark CFGs (with embedded schemas/regex), `select()`, and
interleaved Python control flow between constrained generations inside one program.

**Latency claim, two distinct effects.** (a) Fast-forwarded tokens skip forward passes —
documented. (b) An interleaved program keeps one session and one KV cache, where equivalent
multi-step prompting issues N round-trips that each re-prefill the shared prefix. On this machine
(b) dominates: a cold 2.3k-token prefill costs 14.4 s, so each avoided re-prefill is worth more
than an entire generation. *(b) is architectural — I did not benchmark it in isolation.*

### Integration path — what actually works

| Target | Verdict | Evidence |
|---|---|---|
| **Ollama** | ✗ | No logit access. (Ollama's own `format` JSON-schema output is tier 2, not Guidance.) |
| **Bonsai demo / `llama-server` HTTP endpoint** | ✗ for Python Guidance | Cannot mask logits through a chat endpoint; token healing is documented LlamaCpp/Transformers-only. |
| **`llama-cpp-python` vs the fork** | ✗ **Broken — measured, not assumed** | Wheel **builds** (`llama_cpp_python-0.3.34`, Vulkan on). Import **fails**: `libllama.so: undefined symbol: llama_ftype_name`. lcpy pins upstream `e3546c7` (2026-07-11) and binds that symbol at `llama_cpp.py:1281`; the fork (HEAD 2026-07-18) does not export it (`nm -D` → 0 matches; absent from `llama.h`). Fixing means patching ctypes bindings against a branch that moved **today**. |
| **`-DLLAMA_LLGUIDANCE=ON`** | ✓ **The rung the brief missed** | The fork carries the upstream option (`CMakeLists.txt:120`). Compiles **Guidance's own engine into `llama-server`**, exposing Lark CFG/regex/JSON-schema **server-side over plain HTTP** — no Python logit access, no ABI coupling. Not needed for the current decision; the escape hatch if CFG-level structure is ever required. |

The real ladder is not "GBNF *or* Guidance". It is: native `tool_calls` → built-in
`json_schema`/GBNF → **llguidance inside the server** → Python Guidance (broken here).

`llama-server` natively exposes `--grammar`, `--grammar-file`, `--json-schema`,
`--json-schema-file`, and per-request `response_format: {type: json_schema}`.

### Measured — 30 prompts, 4 arms, both models

Malformation is judged identically across arms on the parsed result. Semantic accuracy (right
tool, right parameters) is scored separately — **a grammar guarantees shape, never correctness.**

**qwen3:4b**

| arm | malformation | needed salvage | semantic | median latency | out tokens |
|---|---|---|---|---|---|
| unconstrained | 0.000 | 0 | 0.567 | 1.723 s | 25.5 |
| native `tool_calls` | **0.133** | 4 | 0.767 | 2.679 s | 35.0 |
| `json_schema` (naive) | 0.000 | 0 | 0.567 | 1.714 s | 25.5 |
| **`json_schema` (per-tool)** | **0.000** | 0 | **0.733** | **1.913 s** | 27.0 |

**Bonsai 8B**

| arm | malformation | needed salvage | semantic | median latency | out tokens |
|---|---|---|---|---|---|
| unconstrained | 0.000 | 0 | 0.400 | 2.398 s | 26.0 |
| native `tool_calls` | 0.000 | 0 | **0.867** | 2.909 s | 28.5 |
| `json_schema` (naive) | 0.000 | 0 | 0.400 | 2.394 s | 26.0 |
| **`json_schema` (per-tool)** | 0.000 | 0 | 0.800 | 2.414 s | 26.0 |

Three findings, in order of how much they change the answer:

**1. Raw malformation is already ~zero — the problem the brief poses barely exists.**
Unconstrained free-text JSON produced 0.000 malformation on *both* models over 30 prompts. There
is no JSON-syntax crisis to solve.

**2. The naive schema is worthless, and it is the one everybody writes.**
`{tool: enum, arguments: object}` scored *identically* to unconstrained on both models (0.567 /
0.400). It constrains the envelope while leaving `arguments` free-form, so the model emits
flawless JSON with **invented parameter names**:

| prompt | naive/unconstrained | correct (native) |
|---|---|---|
| "Pull up the full text of message m_8812." | `{"message_id": "m_8812"}` | `{"id": "m_8812"}` |
| "Am I free on August 3rd?" | `{"date": "2026-08-03"}` | `{"start": …, "end": …}` |
| "…between September 1 and September 30?" | `{"start_date":…, "end_date":…}` | `{"start":…, "end":…}` |
| "Add a todo to renew the car registration…" | `{"description":…, "due_date":…}` | `{"text":…, "due":…}` |
| "Mark todo 42 as done." | `{"todo_id": 42}` | `{"id": 42}` |

**Shape-valid and useless.** Getting value requires a discriminated union over the 13 real tools
with `additionalProperties: false` — that arm recovers semantic accuracy from 0.567 → 0.733
(qwen3) and 0.400 → 0.800 (Bonsai).

**3. Native `tool_calls` does not guarantee well-formedness — it can refuse.**
qwen3's 4 "malformed" native cases are **capability denials**, not broken JSON:

> "I don't have access to your calendar events or schedule…"
> "I don't have access to your existing calendar or reminders…"

— emitted while `list_events` and `add_todo` were attached. (One, "What did I get on July 8th?",
it read as a question about *grades*.) Bonsai never did this: 0.000 on the same arm.

### Which tier, and what ruled out the cheaper one

**Tier 2 — llama.cpp `json_schema` with a per-tool discriminated union.**

- **Tier 1 (native alone) is ruled out empirically**, but not for the reason the brief expects.
  Its malformation is not JSON breakage; it is a **13.3% refusal rate on qwen3** — the model
  declining to use tools it was handed. It also costs the most latency of any arm (2.679 s /
  2.909 s) because it emits the most tokens.

  > **Superseded — see Correction above.** That 13.3% was a *description* defect, not a decoding
  > one, and it is now fixed in the prompt at zero latency cost. Constrained decoding was the
  > wrong instrument for it: the model was emitting perfectly well-formed prose declining to
  > help, and a grammar that forbids prose converts an honest refusal into a confident wrong
  > call (measured below: 2 of qwen3's 4 refusals became fabricated calls under tier 2). **Do not
  > adopt constrained decoding for tool dispatch on the strength of this number.** The tier-2
  > finding still stands for the JSON-emitting sites (`event_create`, triage), which are a
  > different code path with no refusal failure mode.
- **Tier 2 clears the bar** on both models: 0.000 malformation, semantic accuracy within
  0.03–0.07 of native, and **0.5–0.8 s faster** than native. On this hardware "negligible latency
  cost" understates it — the constrained arm is *cheaper*, because a grammar that forbids prose
  stops the model writing any.
- **Tier 3 (Guidance) is ruled out twice over**: the Python path does not load against this fork,
  and nothing in the measurements needs a CFG. Reach for it only if the two-tier memory system
  later wants genuinely interleaved extraction — and even then, prefer
  `-DLLAMA_LLGUIDANCE=ON` server-side over the Python binding.

**The caveat that matters more than the ranking.** Forcing a schema removes the model's option to
decline. On the 4 qwen3 refusals it produced 2 correct calls and **2 confident, well-formed,
wrong ones**:

| prompt | forced result |
|---|---|
| "Am I free on August 3rd?" | ✅ `list_events{start: 2026-08-03T00:00:00, end: 2026-08-04T00:00:00}` |
| "When's my next dentist appointment?" | ✅ `list_events{…}` |
| "Remind me to call the dentist." | ❌ `list_events{today}` — should be `add_todo` |
| "What did I get on July 8th?" | ❌ `search_email{query: "subject:'July 8th' OR body:'grade'…"}` |

This runs **against Lumen's existing design philosophy**: `triage.py` deliberately "leaves the
message honestly uncategorized" rather than guess. Constrained decoding converts honest refusal
into confident error. **Apply it per-site, only where routing has already decided a tool is
needed** — never as a global default.

### What Lumen's repair surface actually is

The brief assumes retry/repair logic to delete. Checked: **Lumen has no retry loops.** It has
hand-rolled JSON *salvage*:

| Location | Pattern |
|---|---|
| `llm/event_create.py:30` `parse_proposal` | Balanced-brace scanner. Docstring: *"First balanced JSON object in the reply — models wrap JSON in prose."* |
| `llm/triage.py:49` | Regex-extract `{…}`, parse, **return `None` on failure** |
| `llm/label_suggest.py:28` | Same |
| `llm/commitments.py:38` | Same |
| `llm/distill.py:52` | `except ValueError` |
| `llm/client.py:124` | Tool-arg `json.loads` with `ValueError` fallback |

**Failure mode is a silent drop, not a retry** — a malformed triage reply leaves a message
uncategorized; a malformed proposal is discarded. So the payoff is *fewer silently dropped
results*, not *saved retry latency* — weaker than the brief assumes, and weaker still given
measured malformation is already ~0.

Note also these are all **structured-extraction** paths. Tool *dispatch* already goes through
native `tool_calls` in `client.py`. The two problems are separate and only the first is a grammar
problem — which is exactly where the brief guessed right: the value is in structured extraction,
not tool dispatch.

---

## Open questions

1. ~~**Why does qwen3:4b refuse on 3–4 prompts?**~~ **ANSWERED 2026-07-18 — a schema-description
   problem, and a prompt fix closed it.** Three causes, all in text the model reads, none in the
   model:

   - `list_events` was described as listing "the user's **Google Calendar**" — a remote service
     the model reads as an account it has no credentials for. `search_email` says "the user's
     locally mirrored email" and was never once refused. That asymmetry was the whole signal.
   - The same docstring said "**Use this only** for dates the assistant's calendar context
     doesn't already cover", and `calendar_context` said events outside the window "are not
     shown — **say so if asked** about them". Between them the model was told when to withhold
     the tool and handed a sanctioned way to decline.
   - The one that only live measurement caught: `list_events` takes a date range and affords **no
     keyword search**, so "when is my next dentist appointment?" is genuinely unanswerable as the
     model read the tool — and it expressed that as *"I don't have access to your medical
     appointments"*. Rewriting the first two moved calendar refusals 4/4 → 3/4; stating the
     affordance ("to find an event by topic, list a wide range and read the titles") took it to
     0/4.

   **Bonsai's last advantage disappears**, as anticipated: the correctness gap was this refusal
   set, and it is closed in the prompt. Guarded by `tests/eval/` going forward.
2. **KV4 fidelity unmeasured.** I used `-ctk/-ctv q4_0` throughout per the brief but never
   verified the "negligible divergence" claim against fp16 KV. The fork also ships
   `--kv-mean-center` — a calibrated per-channel bias for Q4_0 K-caches — which the brief never
   mentions and which presumably exists *because* naive Q4_0 KV does diverge. Note the qwen3
   configs ran **fp16 KV**, so the memory comparison (7 612 vs 3 840 MiB) conflates quantization
   of weights and of KV.
3. **Desktop responsiveness under load was not measured.** I logged fans and temps but never ran
   a controlled interaction-latency probe, so the bus-contention question the brief raises is
   unanswered.
4. **27B is untested beyond the crash.** Whether a shorter context, `-ngl` under 99, or a smaller
   batch avoids the device loss is unknown — I did not retry after the abort.
5. **Sample sizes are small.** 20 prompts for correctness, 30 for the constrained arms, 3–5 reps
   per latency point. A 0.03–0.07 semantic gap between arms is inside the noise floor at n=30;
   the malformation and latency findings are robust, the fine-grained accuracy ranking is not.
