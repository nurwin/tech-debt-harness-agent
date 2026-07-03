# plan.md — Production Agent Harness for Multi-File Refactoring

> **For Claude Code.** Build this project from scratch. Work top-to-bottom through the
> phases. Each phase has a **Definition of Done** and an explicit **verification command**
> — do not advance until it passes. Prefer small, committed increments.
>
> **Architecture in one line:** LangGraph is the **orchestrator** (state machine, loops,
> checkpointing, HITL). **Pi** (pi.dev coding agent) is the **executor** that performs the
> actual code edits, invoked headless from inside a per-tenant Docker sandbox. A **React
> web UI** drives the human-in-the-loop gates and shows live telemetry.

---

## 0. Context & grading (read first)

This is a take-home for an "AI Harness Engineer" role. Scoring is **80% harness / 20%
prompt**. The graders care about the orchestrator, the state machine, verification loops,
crash-resilience, sandbox security, telemetry, and deployment architecture — NOT about how
clever the refactor is. Optimize every decision for *"could this run thousands of
concurrent agent runs in production without losing money or leaking tenant data?"*

Scoring weights to keep in mind while building:
- System Architecture 25% — clear planner/executor/verifier split; justified state tracking.
- Execution Quality 35% — safe mid-run crash handling; genuinely secure sandbox; local
  setup flawless in <15 min.
- Data & Telemetry 20% — accurate token + span tracing; demo proves real-time execution
  and failure modes.
- Pragmatic Engineering 20% — solved multi-agent sync without over-engineering.

**Non-negotiable requirements** (from the brief):
1. Three stages: Planner (read-only, builds JSON plan + dep graph), Executor (sandboxed
   file mutations), Verifier (runs tests + linter, feeds failures back).
2. Verification loop **hard-aborts + rolls back after 3 iterations** (cost guardrail).
3. Crash recovery: server dies on step 4 of 10 → resume without restarting or
   duplicating LLM cost.
4. Telemetry: export traces with span durations, token usage per agent node, and a
   visual breakdown of verification-loop iterations.
5. Deployment: Dockerfile + compose/manifests; explain horizontal scaling and how tenant
   codebases are isolated so Agent A cannot read Agent B's filesystem.

**Our additions:** three human-in-the-loop approval gates + a React web UI.

---

## 1. Tech stack (do not deviate without reason)

- **Orchestrator:** Python 3.12 + **LangGraph** (`StateGraph` + `SqliteSaver` checkpointer;
  Postgres saver documented for prod).
- **Executor:** **Pi coding agent** — package **`@earendil-works/pi-coding-agent`** (repo
  `earendil-works/pi`; NOT the deprecated `@mariozechner/*` name). Pin a known-good version
  (as of writing, latest is `v0.80.3`, 2026-06-30); pin the exact version in the Dockerfile
  so builds are reproducible. Invoked **headless via RPC mode**
  (`pi --mode rpc --no-session`) inside the sandbox container. Pi's RPC mode is a
  JSON-over-stdin/stdout protocol built for embedding in other apps. Non-interactive modes
  (`-p`, `--mode json`, `--mode rpc`) do NOT show a trust prompt, which is what makes
  headless operation possible.
  - Fallback executor: `pi -p --mode json "<prompt>"` (one-shot print/JSON) if RPC framing
    proves fiddly. Keep the executor behind an interface so either works.
  - **Critical security fact:** Pi has NO built-in permission system — by default it runs
    with the full permissions of the launching user. The Docker container IS the security
    boundary (Pi's own docs mandate containerizing for isolation). This makes the sandbox
    layer non-optional, not a nicety.
  - For a Node/TS host you'd embed `@earendil-works/pi-agent-core` directly; since our
    orchestrator is Python, we drive Pi as a subprocess over RPC.
- **Sandbox:** one ephemeral Docker container per `(tenant, run)`, network-isolated.
- **API:** FastAPI (serves REST + WebSocket for live gate/telemetry updates).
- **Web UI:** React + Vite + Tailwind (single-page; talks to FastAPI over REST + WS).
- **Telemetry:** OpenTelemetry → Jaeger (OTLP HTTP).
- **Target repo:** a small Python FastAPI/Flask app with deliberate debt (vendored into
  `test-fixtures/` so the demo is deterministic and offline).

> **Executor design rule.** Wrap the executor behind an abstract `ExecutorAdapter` with a
> `PiAdapter` implementation. This lets you demo "runtime-agnostic executor" and swap Pi
> for Claude Code/Codex later. This is a strong architecture talking point — build it.

---

## 2. Repository layout to create

```
refactor-harness/
├── plan.md                      # this file
├── README.md                    # write LAST; includes arch diagram + deploy brief
├── docker-compose.yml           # harness + jaeger + web (dev)
├── .env.example
├── orchestrator/                # Python service
│   ├── pyproject.toml
│   ├── src/
│   │   ├── state.py             # HarnessState TypedDict + constructors
│   │   ├── graph.py             # StateGraph wiring + checkpointer
│   │   ├── config.py            # env, limits, MAX_ITERATIONS=3
│   │   ├── agents/
│   │   │   ├── prompts.py       # planner + executor system prompts (the 20%)
│   │   │   ├── planner.py       # Stage 1 node (read-only)
│   │   │   ├── executor.py      # Stage 2 node (drives Pi in sandbox)
│   │   │   ├── verifier.py      # Stage 3 node (tests+lint, loop router)
│   │   │   ├── gates.py         # 3 HITL interrupt gates
│   │   │   └── terminal.py      # finalizer + aborted nodes
│   │   ├── executor_adapters/
│   │   │   ├── base.py          # ExecutorAdapter ABC
│   │   │   ├── pi_adapter.py    # drives `pi --mode rpc` in the sandbox
│   │   │   └── dryrun_adapter.py# deterministic, no-LLM adapter for tests/CI
│   │   ├── sandbox/
│   │   │   └── sandbox.py       # per-tenant Docker container mgr + tools
│   │   ├── telemetry/
│   │   │   └── trace.py         # OTel spans → Jaeger
│   │   └── api/
│   │       ├── server.py        # FastAPI: REST + WebSocket
│   │       └── schemas.py       # pydantic request/response models
│   └── tests/
│       ├── test_state.py
│       ├── test_graph_dryrun.py # full run using dryrun adapter (no API key)
│       └── test_crash_resume.py # kill + resume assertion
├── executor-image/              # the sandbox Docker image (has pi + pytest + ruff)
│   └── Dockerfile
├── web/                         # React UI
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api.ts               # REST + WS client
│       ├── components/
│       │   ├── RunList.tsx
│       │   ├── RunDetail.tsx
│       │   ├── PlanGate.tsx     # approve/edit/reject the JSON plan
│       │   ├── EscalationGate.tsx
│       │   ├── MergeGate.tsx    # review final diff
│       │   ├── PipelineView.tsx # planner→executor→verifier live state
│       │   ├── TokenMeter.tsx   # per-node token usage
│       │   └── LoopTimeline.tsx # verification iterations
│       └── styles.css
├── infra/
│   ├── Dockerfile.orchestrator
│   ├── k8s/                     # Job-per-run manifests (prod isolation story)
│   │   ├── job-template.yaml
│   │   ├── deployment-api.yaml
│   │   └── networkpolicy.yaml
│   └── terraform/               # OPTIONAL, skip if short on time
└── test-fixtures/
    └── sample-repo/             # deliberate-debt app + failing tests
```

---

## 3. Shared state schema (build FIRST — everything depends on it)

`orchestrator/src/state.py` — a `HarnessState` TypedDict threaded through every node and
checkpointed after every transition. Required fields:

- Identity: `thread_id`, `tenant_id`, `repo_path` (path INSIDE sandbox), `auto_approve: bool`.
- Planning: `plan: list[PlanStep]`, `current_step: int`, `completed_steps: list[int]`.
  - `PlanStep = {step_id, file, change_type, rationale, status}`.
- Loop: `iteration_count: int`, `escalation_count: int`, `error_log: list[ErrorRecord]`.
  - `ErrorRecord = {step_id, iteration, stdout, stderr, failed_tests, timestamp}` (structured,
    NOT a raw blob).
- HITL: `pending_approval: "plan"|"escalation"|"merge"|None`, `human_decision: dict|None`,
  `approval_history: list[HumanDecision]` (audit trail: gate, action, actor, timestamp).
- Cost: `token_usage: {planner, executor, verifier, total}`.
- Status: `status: "planning"|"awaiting_human"|"executing"|"verifying"|"finalizing"
  |"succeeded"|"failed"|"aborted"`.
- Output: `final_diff: str|None`, `failure_reason: str|None`.

Constants: `MAX_ITERATIONS = 3`. Helper `new_state(...)` and `now_iso()`.

**DoD:** `test_state.py` constructs a fresh state and asserts defaults.
**Verify:** `cd orchestrator && python -m pytest tests/test_state.py -q`

---

## 4. Phase-by-phase build

> Commit after each phase. Keep the dryrun adapter working at every step so the full graph
> is testable without an API key or Pi installed.

### Phase A — Sandbox layer (tenant isolation = top security score)
File: `sandbox/sandbox.py`. One class `Sandbox(tenant_id, thread_id, host_repo_path)`.

- `start()`: `docker run -d` with ALL of: `--network none`, `--read-only` (+ `--tmpfs /tmp`),
  `--user 1000:1000`, `--cap-drop ALL`, `--security-opt no-new-privileges`,
  `--memory 512m`, `--cpus 1.0`, volume `-v {host_repo_path}:/workspace/{tenant_id}:rw`,
  workdir `/workspace/{tenant_id}`. Container name `sbx-{tenant}-{thread}`.
- On start, create a git baseline commit inside the container (`__harness_baseline__`) for
  rollback + diffing.
- Tools (all via `docker exec`, never touching host FS): `read_file`, `write_file`,
  `search_replace`, `run_tests` (`pytest -q`), `run_lint` (`ruff check .`), `diff`
  (`git diff __harness_baseline__`), `rollback` (`git reset --hard` + `git clean -fdq`).
- `start_pi_rpc()`: launch `pi --mode rpc --no-session` inside the container as a
  long-lived process (see Phase C); expose send/recv over its stdio.
- `teardown()`: `docker rm -f` — always call in a `finally`.

**Why isolation works (for README):** each run's container has a different host dir mounted
at a tenant-namespaced path with `--network none`; Agent A has no mount, route, or namespace
overlapping Agent B, so it cannot read B's filesystem.

**DoD:** integration test starts a sandbox against `test-fixtures/sample-repo`, writes a
file, reads it back, runs tests, tears down.
**Verify:** `python -m pytest tests/test_sandbox.py -q` (skips gracefully if Docker absent).

### Phase B — Executor adapter interface + DryRun adapter
Files: `executor_adapters/base.py`, `executor_adapters/dryrun_adapter.py`.

- `ExecutorAdapter.apply_step(sandbox, step, prior_error) -> ExecutorResult` where
  `ExecutorResult = {action, file, tokens, raw}`.
- `DryRunMulticaAdapter`-style deterministic adapter: applies a **hardcoded correct fix**
  for the fixture repo (e.g. inserts the missing validation), reports `tokens=0`. This lets
  the ENTIRE graph — loops, gates, crash-resume — be tested in CI with no LLM and no Pi.

**DoD:** unit test calls the dryrun adapter and asserts the fixture file is edited.
**Verify:** `python -m pytest tests/test_dryrun_adapter.py -q`

### Phase C — Pi executor adapter (the Pi integration)
File: `executor_adapters/pi_adapter.py`. This is the core integration.

- The sandbox image (`executor-image/Dockerfile`) installs pi globally, pinned to an exact
  version for reproducibility:
  `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.80.3`
  (`--ignore-scripts` is Pi's own recommended install flag). Also install `pytest`, `ruff`,
  `git`, and a Python runtime. Provider key passed as env (`ANTHROPIC_API_KEY`) at
  `docker run`. Verify the install in the image build with `pi --version`.
- `PiAdapter.apply_step`:
  1. Build a focused prompt from the plan step + the file contents + (on retry) the
     structured previous error. Use the executor system prompt from `prompts.py`.
  2. Drive pi over **RPC**: send `{"type":"prompt","message": <prompt>}` on stdin; read
     JSONL events from stdout until `{"type":"agent_end"}`. **Split on `\n` only** — do NOT
     use a generic line reader; pi's RPC is strict LF-delimited JSONL and generic readers
     mis-split on U+2028/U+2029 inside JSON strings.
  3. Pi edits files directly in `/workspace/{tenant}` using its own read/write/edit/bash
     tools — that's the point: Pi is the executor.
  4. Extract token usage from pi's events/session for `token_usage.executor`. If unavailable
     via RPC, fall back to `pi -p --mode json` which reports cost/usage per invocation.
- Constrain Pi to the workspace: run it with `--tools read,write,edit,bash` and rely on the
  container's `--network none` + read-only root for safety (Pi has no permission popups by
  design — the container IS the sandbox).
- Config select-able: `EXECUTOR_ADAPTER=pi|dryrun` env var chooses the adapter.

**DoD:** with a real key + Docker, a single-file refactor via Pi passes the fixture tests.
**Verify (manual):** documented in README; CI uses dryrun.

### Phase D — Planner node (Stage 1, read-only)
File: `agents/planner.py`. Read-only: it may `read_file`/`ls`/`grep` in the sandbox but has
NO write tool. Uses an LLM call (Anthropic) with `PLANNER_SYSTEM` to emit **strict JSON**:
`{"steps":[{step_id, file, change_type, rationale}]}`, ordered leaf-first via a simple
dependency scan (Python `ast` imports; order files with fewest dependents first). Validate
with pydantic; reject malformed plans before execution. Tally planner tokens. Set
`status="awaiting_human"`, `pending_approval="plan"`.

**DoD:** dryrun planner (or stubbed LLM) yields a valid 1-step plan for the fixture.
**Verify:** covered by `test_graph_dryrun.py`.

### Phase E — Executor node (Stage 2)
File: `agents/executor.py`. Pops `plan[current_step]`, reads the file, builds retry context
from the last matching `ErrorRecord`, calls the selected `ExecutorAdapter.apply_step`,
tallies executor tokens, marks step `in_progress`, sets `status="verifying"`.

### Phase F — Verifier node + loop router (Stage 3)
File: `agents/verifier.py`. Runs `run_tests` + `run_lint` in the sandbox.
- **Pass:** mark step `done`, append to `completed_steps`, `current_step += 1`, reset
  `iteration_count = 0`, `status="executing"`.
- **Fail:** append structured `ErrorRecord`, `iteration_count += 1`.
- **Router** (conditional edge after verifier):
  - passed & more steps → `executor`
  - passed & plan exhausted → `merge_gate`
  - failed & `iteration_count < 3` → `executor` (self-correct)
  - failed & `iteration_count >= 3` → `escalation_gate`

**DoD:** loop demonstrably self-corrects and stops at 3.
**Verify:** `test_graph_dryrun.py` includes a "poisoned" adapter variant that fails twice
then succeeds, and one that always fails (asserts abort at 3 + rollback).

### Phase G — HITL gates (interrupt-based)
File: `agents/gates.py`. Use LangGraph `interrupt()` + `Command(goto=..., update=...)`.
All gates are bypassed when `auto_approve=True` (per-tenant policy for high concurrency).

1. **plan_gate** — after planner. `interrupt()` surfaces the plan. Human action
   `approve|edit|reject`. edit → replace plan from payload; reject → `aborted`.
2. **escalation_gate** — on loop exhaustion (replaces silent abort). Surfaces the error
   trail. Action `retry|abort|accept_partial`. retry → reset `iteration_count=0` once,
   bump `escalation_count`; accept_partial → `finalizer` on completed files; abort →
   rollback + `aborted`. **Auto-rollback still fires under auto_approve / no response**, so
   the cost guardrail is never weakened — HITL is an override, not a hole.
3. **merge_gate** — before finalizer. Surfaces `sandbox.diff()`. Action `approve|reject`.
   reject → rollback + `aborted`.

Every decision appends a `HumanDecision` to `approval_history` (audit trail).

**DoD:** dryrun run pauses at plan_gate; resuming with `approve` proceeds to completion.
**Verify:** `test_graph_dryrun.py` drives all three gates programmatically.

### Phase H — Terminal nodes
File: `agents/terminal.py`. `finalizer` computes `final_diff`, sets `succeeded`, and (prod)
opens a PR via GitHub API — for the demo, emit a `.patch` + PR-body text so no live auth is
needed. `aborted` records `failure_reason`.

### Phase I — Graph assembly + checkpointer (crash recovery)
File: `graph.py`. Wire nodes and conditional edges. Attach `SqliteSaver` (path from env;
Postgres saver documented for prod). START→planner→plan_gate; executor→verifier
(conditional router); gates emit `Command(goto=...)`; finalizer/aborted→END.

Expose:
- `run(state)` — invoke to first interrupt or END.
- `resume(thread_id, command=None)` — continue from last checkpoint. **The same call path
  serves BOTH crash recovery (`command=None`) and human decisions
  (`command=Command(resume=decision)`)** — call this out; it's the resilience story.

**DoD (crash test):** `test_crash_resume.py` runs a multi-step dryrun refactor, simulates a
crash by discarding the in-memory graph after step N, rebuilds the graph from the same
SQLite file, resumes, and asserts: (a) it completes, (b) already-`completed_steps` are NOT
re-executed (assert adapter call count), (c) token totals didn't double.
**Verify:** `python -m pytest tests/test_crash_resume.py -q`

### Phase J — Telemetry (OTel → Jaeger)
File: `telemetry/trace.py`. `@contextmanager span(name, thread_id)` wrapping each node with
attributes: `duration_ms`, per-node `token_usage`, verification `iteration`, and on gate
spans `gate` + `human_wait_duration`. Export via OTLP HTTP to Jaeger. No-op fallback when
`OTEL_EXPORTER_OTLP_ENDPOINT` unset so offline runs still work.

**DoD:** a run produces a Jaeger trace where a failed refactor visibly shows 3
executor→verifier span pairs (the loop breakdown they ask for).
**Verify (manual):** open `http://localhost:16686`, service `refactor-harness`.

### Phase K — FastAPI (REST + WebSocket)
Files: `api/server.py`, `api/schemas.py`.
- REST: `POST /runs` (start), `GET /runs` (list), `GET /runs/{id}/state`,
  `GET /runs/{id}/pending` (current interrupt payload), `POST /runs/{id}/decision`
  (submit gate decision → resume graph), `GET /runs/{id}/trace` (Jaeger deep link),
  `GET /runs/{id}/diff`.
- **WebSocket** `/ws/runs/{id}`: push status changes, token updates, and gate-pending events
  so the UI updates live without polling.
- Run the graph in a worker task; persist enough in state to rebuild the sandbox handle on
  resume (store `tenant_id` + host path).

**DoD:** curl start → pending → decision → succeeded, end to end (dryrun adapter).
**Verify:** `python -m pytest tests/test_api_dryrun.py -q`

### Phase L — React Web UI
Files under `web/`. Vite + React + Tailwind. Talks to FastAPI (REST + WS). Views:
- **RunList** — start a run (tenant, repo, auto_approve toggle) + list with status badges.
- **RunDetail** — live **PipelineView** (planner→executor→verifier, current node
  highlighted), **TokenMeter** (per-node bars + total), **LoopTimeline** (one chip per
  verification iteration; red on fail, green on pass).
- **Gate panels** (appear when `pending_approval` set):
  - **PlanGate** — render JSON plan as an editable table; Approve / Edit / Reject.
  - **EscalationGate** — show last errors; Retry / Abort / Accept-partial.
  - **MergeGate** — render final diff (syntax-highlighted); Approve / Reject.
- Link out to the Jaeger trace.

Keep it clean and functional — this is a differentiator (most candidates ship CLI-only) but
must not eat the time budget. No auth, no router library needed (single page + state).

**DoD:** from the browser, start a dryrun run, approve the plan, approve the merge, watch it
reach "succeeded" with live token + loop updates.
**Verify (manual):** `docker compose up`, open the web port, complete a run.

### Phase M — Infra + deployment brief
- `infra/Dockerfile.orchestrator`, `executor-image/Dockerfile`, root `docker-compose.yml`
  (orchestrator API + jaeger + web; mounts docker socket for local sandbox spawning; mounts
  `test-fixtures` as tenant repos).
- `infra/k8s/`: a **Job-per-run** template (prod replaces the local docker-socket spawn),
  a `deployment-api.yaml`, and a `networkpolicy.yaml` denying pod-to-pod traffic (the prod
  tenant-isolation control). Terraform is OPTIONAL — skip if short on time.
- README **deploy brief** answering the three Part-2 questions explicitly (state/resilience
  via checkpointer; telemetry via OTel→Jaeger; scaling via stateless workers + one
  sandbox/Job per run + network isolation + namespaced volumes).

### Phase N — README (write last)
Include: architecture diagram (ASCII is fine), the Planner/Executor/Verifier + 3-gate flow,
**why Pi is the executor and LangGraph the orchestrator**, run-in-under-15-min instructions
(dryrun path needs NO API key), state-recovery + cost-control decisions, and the Part-2
deployment brief. Explicitly state the executor is adapter-based (Pi today, Claude
Code/Codex swappable).

---

## 5. The 20% — prompts (`agents/prompts.py`)

- `PLANNER_SYSTEM`: read-only role; output ONLY strict JSON plan; order leaf-first; one step
  per file; minimal surgical changes.
- `EXECUTOR_SYSTEM` (fed to Pi): apply ONE change to ONE file; smallest change satisfying the
  rationale; on retry, fix the specific failure from the provided error; preserve unrelated
  code. Keep these tight and stable — do not over-tune.

---

## 6. Test fixture (`test-fixtures/sample-repo/`)

A tiny FastAPI/Flask app with deliberate debt: one endpoint lacking input validation and a
synchronous blocking call. Include `test_app.py` whose tests FAIL until the refactor adds
validation (so the verification loop has something real to converge on). The DryRun adapter
knows the correct fix so CI is deterministic.

---

## 7. Definition of Done for the whole project

- [ ] `docker compose up` brings up API + Jaeger + web; full run from the browser in <15 min.
- [ ] Full dryrun pipeline passes in CI with NO API key and NO Pi installed.
- [ ] Real run uses **Pi** as the executor inside the sandbox and passes fixture tests.
- [ ] Verification loop self-corrects and hard-aborts + rolls back at exactly 3 iterations.
- [ ] Crash-resume test proves no step re-execution and no token doubling.
- [ ] All three HITL gates work from the web UI (approve/edit/reject, retry/abort/partial,
      merge approve/reject) with an audit trail.
- [ ] Jaeger shows per-node token usage, span durations, and the loop-iteration breakdown.
- [ ] Tenant isolation demonstrable: two concurrent runs, separate containers, `network=none`,
      namespaced volumes; A cannot see B.
- [ ] README explains architecture, Pi-as-executor rationale, state recovery, cost controls,
      and the scaling/isolation deploy brief.

---

## 8. Time budget (~8–10h; trim in this order if short)

1. State + graph + dryrun adapter + gates + crash-resume test  (core 80% — do NOT cut)
2. Sandbox + Pi adapter                                         (the integration)
3. Verifier loop + telemetry                                    (35% + 20%)
4. FastAPI + WebSocket                                          (enables UI)
5. React UI                                                     (differentiator)
6. Infra + README                                              (architecture score)
7. **Trim first if needed:** Terraform, K8s NetworkPolicy polish, planner dep-graph
   sophistication, Pi token-usage extraction refinement (fall back to `pi -p --mode json`).

---

## 9. Build order rule for Claude Code

Always keep the **dryrun path green**. Build state → graph → gates → crash-resume with the
dryrun adapter FIRST and get the full loop working end-to-end with zero external
dependencies. Only THEN wire in Docker, Pi, telemetry, API, and the web UI. This guarantees
a working submission even if the Pi/Docker integration hits environment friction, and makes
every later phase independently verifiable.
