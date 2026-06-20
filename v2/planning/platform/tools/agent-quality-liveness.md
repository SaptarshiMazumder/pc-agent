# Answer Quality + Liveness — Architecture

**Status:** implemented.
**Goal:** add quality + liveness checks **without coupling any of them to the loop's
logic** — each is self-contained, toggled by one config flag, and OFF is byte-for-byte
today's behavior.

Companion diagram: [`agent-quality-liveness.puml`](agent-quality-liveness.puml).

The four pieces: **#1** completeness self-check (prompt), **#2** answer verifier (now the
`verify_answer` **tool**), **#3** call-rate brake, **#4** no-progress detector.

---

## 1. Two concerns, two DIFFERENT seams

| Concern | Question | Seam | OFF = |
|---|---|---|---|
| **Liveness** | "is it stuck / looping / not moving?" | `RunObserver` observers on the **loop** | empty observer list → hooks are no-ops |
| **Quality** | "is the answer good / complete / honest?" | the agent-invoked **`verify_answer` tool** | tool not registered → as if it never existed |

The important shift from the first draft: **quality is a tool, not a loop hook.** A
loop-hook verifier ran *after* the answer was already streamed, so the user saw the
answer and then a second *"I apologize…"* answer. As a tool, the agent verifies its
**draft** first, fixes issues silently, and sends **one** clean answer. The loop knows
nothing about it.

---

## 2. The decoupling contract

1. **Loop ↔ liveness:** the loop depends only on the `RunObserver` port (3 generic hook
   calls: `on_tool` before/after, `on_turn`). It has **zero** knowledge of quality.
2. **Quality is just a tool:** `verify_answer` is a normal `Tool`. When `verify_tool` is
   off it is **not added to the registry** — fully absent, nothing references it.
3. **Each checker is self-contained:** an observer sees only a `ToolEvent`; the verifier
   needs only an injected judge-LLM fn + a rubric.
4. **Defaults are no-op / absent;** wiring is one place (composition root). Add/remove
   either via one config flag. Nothing is coupled.
5. **Fail-open:** the judge errors → the tool returns PASS; an observer errors → ignored.

---

## 3. Liveness (#3, #4) — observers on the loop

`application/interfaces/run_observer.py`:
```python
class RunObserver(Protocol):
    def on_tool(self, ev: ToolEvent) -> str | None: ...   # halt reason or None
    def on_turn(self, i: int) -> str | None: ...
    def reset(self) -> None: ...
```
- **`CallRateBrake` (#3):** same tool called > N times in the last M calls (any args) → halt.
- **`NoProgressDetector` (#4):** K tool-using turns with no new result → halt.
- A halt injects a `[liveness]` steering message and continues; after `STUCK_CAP` halts
  the run stops. *(existing GuardedTool loop-detection stays as the per-tool, identical-args
  inner layer — complementary.)*

`build_observers(config)` → `[]` by default (`AGENTD_LIVENESS=callrate,noprogress` to enable).

---

## 4. Quality (#2) — the `verify_answer` tool

`tools/verify_tool.py` — a `Tool` that wraps a `Verifier`:
- params: `answer` (required draft), optional `task`, optional `evidence`.
- returns `PASS` or `NEEDS WORK: <issues>` (with "fix silently, don't apologize").
- wraps **`LlmJudgeVerifier`** (`verify/llm_judge.py`) — an out-of-band judge grading
  completeness / evidence / no-fabrication; depends only on an injected judge-LLM fn
  (`build_judge_fn`, the one place that knows LiteLLM); **fail-open**.

Registered in `build_tools` **only when `config.verify_tool`** is true; otherwise the tool
isn't created. The prompt adds a `## Verify Before You Send` step **only when the tool is
present**.

> Trade-off: a tool is **agent-invoked (advisory)**, not loop-enforced — reliability
> depends on the model calling it, steered by the prompt + completeness rule. This is the
> deliberate cost of the clean single-answer UX (and is how Claude-style self-review works).

---

## 5. Completeness (#1) — prompt section

`## Before You Finish` — an in-band self-check (`AGENTD_COMPLETENESS_CHECK=1`). Cheapest,
weakest (the model checks itself); complements the out-of-band tool.

---

## 6. Config (all default OFF)

| Config | Default | Effect |
|---|---|---|
| `AGENTD_LIVENESS=callrate,noprogress` | `[]` | which liveness observers |
| `AGENTD_VERIFY_TOOL=1` | off | register the `verify_answer` tool |
| `AGENTD_VERIFY_MODEL` | `search_model`→`model` | cheap judge model for the tool |
| `AGENTD_COMPLETENESS_CHECK=1` | off | include the prompt self-check |

---

## 7. Where it lives (no loop coupling for quality)

- Loop: `infrastructure/engine/native.py` — only the observer hooks; **no verifier**.
- Liveness: `application/interfaces/run_observer.py`, `infrastructure/liveness/*`.
- Quality: `application/interfaces/verifier.py`, `infrastructure/verify/{llm_judge,factory}.py`,
  `infrastructure/tools/verify_tool.py`.
- Wiring: `main/container.py` (observers → engine), `infrastructure/tools/__init__.py`
  (tool registered iff `verify_tool`).
