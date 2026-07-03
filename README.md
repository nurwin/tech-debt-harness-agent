# Refactor Harness

A production-shaped agent harness for multi-file code refactoring:
**LangGraph** orchestrates a planner → executor → verifier state machine with
SQLite-checkpointed crash recovery and three human-in-the-loop gates; the
**Pi coding agent** (`@earendil-works/pi-coding-agent`, pinned `0.80.3`) performs the
actual edits, driven headless over LF-delimited JSONL RPC inside a locked-down,
per-`(tenant, run)` Docker sandbox; a **React web UI** drives the gates and shows live
telemetry (OpenTelemetry → Jaeger).

```
                          ┌─────────────────────────────────────────────────────────┐
                          │                  LangGraph orchestrator                 │
                          │              (SqliteSaver checkpoint per node)          │
                          │                                                         │
  POST /runs ──▶ planner ─▶ plan_gate ══▶ executor ──▶ verifier ──┬─ pass+more ─▶ executor
                (read-only,  approve/       │             │       ├─ pass+done ─▶ merge_gate ══▶ finalizer ─▶ .patch
                 JSON plan,  edit/reject    │             │       ├─ fail <3 ───▶ executor        approve/     + PR body
                 dep-ordered)               ▼             │       └─ fail ≥3 ───▶ escalation_gate reject
                          │            ExecutorAdapter    │                        retry×1 / abort /
                          │            (Pi | dryrun)      │                        accept_partial
                          │                 │             │                              │
                          └─────────────────┼─────────────┼──────────────────────────────┘
                                            ▼             ▼                        (abort ⇒ rollback
                              ┌──────────────────────────────────┐                  to git baseline)
                              │  Docker sandbox per (tenant,run) │
                              │  --network none · read-only root │
                              │  non-root · cap-drop ALL · limits│
                              │  /workspace/{tenant} namespaced  │
                              │  pi --mode rpc --no-session      │
                              └──────────────────────────────────┘
        ══▶ = HITL gate (LangGraph interrupt; bypassed per-tenant by auto_approve policy)
```

## Run it (< 15 minutes, no API key needed)

**Full demo (API + Jaeger + web UI):**

```bash
docker build -t refactor-harness-executor:latest executor-image/   # sandbox image (pi pinned)
docker compose up --build -d
open http://localhost:5173        # web UI
open http://localhost:16686      # Jaeger
```

In the UI: **Start refactor run** (adapter `dryrun`, or `flaky` to watch the
verification loop self-correct) → approve the plan at the **plan gate** → watch the
executor/verifier loop and token meters live (WebSocket) → approve the diff at the
**merge gate** → `succeeded`. Try adapter `alwaysfail` to see the loop hard-abort at
3 iterations, roll back, and (with auto-approve off) surface the **escalation gate**.

**Test suite (CI path — no Docker, no Pi, no key):**

```bash
cd orchestrator
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q        # 39 tests; sandbox tests auto-skip without Docker
```

**Real Pi executor (manual, needs a key):** put `ANTHROPIC_API_KEY` in `.env`
(see `.env.example`), rebuild compose, and start a run with adapter `pi`. The planner
switches to a real LLM call and the executor drives Pi over RPC inside the sandbox.

## The three stages

1. **Planner** (read-only): captures the verification baseline (the target repo ships
   deliberately red: 6 failing tests, lint errors), then emits a strict-JSON plan —
   one file per step, ordered leaf-first from an AST import scan (`db.py` before
   `api.py`, which imports it). In Pi mode the plan comes from an LLM and is
   pydantic-validated + re-ordered as a safety net; in dryrun mode the scan itself
   plans, so the whole graph runs offline.
2. **Executor**: applies exactly one step via the `ExecutorAdapter` seam. Retries get
   the previous iteration's structured `ErrorRecord` (failed tests, lint errors,
   output tail) and any human guidance granted at the escalation gate.
3. **Verifier**: runs `pytest` + `ruff` in the sandbox **every iteration**, records
   structured results, and routes: pass→next step, fail<3→self-correct,
   fail≥3→escalation. On step pass it commits the workspace (git), giving
   `accept_partial` step-granular ground truth.

**Verification policy (a deliberate design decision):** a cross-file-atomic refactor
cannot be test-green mid-plan — once `db.py` goes async, every caller in `api.py` is
broken until the next step fixes it, in either order. So intermediate steps are gated
on *no new lint errors vs baseline* (test results still recorded and fed back), and
the **final step requires zero failures and zero lint errors** — the contract. The
tests-as-router behavior (self-correct ×3, abort) is fully exercised at the final step.

## Why LangGraph orchestrates and Pi executes

The graded problem is the harness: state, loops, gates, recovery, isolation, cost.
LangGraph gives those as first-class primitives — `StateGraph` + checkpointer means
every transition is durable; `interrupt()`/`Command` makes HITL a pause, not a poll
loop. Pi is deliberately *only* the executor: it's excellent at "edit this file to
satisfy this rationale" but has **no permission system** (it runs with the launching
user's full privileges), so it is never the trust boundary and never the orchestrator.
It runs headless (`pi --mode rpc --no-session` — non-interactive modes skip the trust
prompt) inside the sandbox, speaking strict LF-delimited JSONL (the reader splits on
`\n` only; U+2028/U+2029 occur inside JSON strings and generic line iterators corrupt
them — there's a test that proves it).

The executor sits behind an `ExecutorAdapter` ABC: `PiAdapter` today; Claude
Code/Codex tomorrow is one new adapter. The dryrun/flaky/alwaysfail adapters make the
entire graph — loops, gates, crash-resume — deterministic and CI-testable with zero
LLM spend.

## Crash recovery (the resilience story)

Every node transition checkpoints to SQLite (`SqliteSaver`; Postgres saver in prod is
a one-line swap). `resume_run(thread_id, command)` serves **both** recovery paths:

- **Crash** (`command=None`): the server dies mid-run → rebuild the graph over the
  same DB file and invoke; completed steps are not re-executed, token totals do not
  double, and a run parked at a gate re-raises the same interrupt.
- **Human decision** (`command=Command(resume=...)`): the gate's `interrupt()`
  returns the decision and the graph continues.

A dead worker and a slow human are the same problem to the graph.
`tests/test_crash_resume.py` asserts all three properties by killing the process
state mid-run (adapter call counts prove no re-execution). The API discovers runs
from the checkpoint DB at startup, so an API restart loses nothing either. The only
rebuilt-on-resume runtime object is the workspace handle, reconstructed from
persisted state (`tenant_id`, `thread_id`, `host_repo_path`) — the workspace itself
and its git baseline live on disk/volume and survive the process.

## Cost controls

- `MAX_ITERATIONS = 3` is a constant, not config. The verifier routes to the
  escalation gate at 3 failed iterations on a step; under `auto_approve` (or any
  non-retry outcome) the run aborts and **rolls back to the git baseline**.
- The escalation gate can grant exactly **one** human retry (`MAX_ESCALATIONS = 1`);
  a second retry request is refused and aborts. HITL is an override, not a hole.
- The plan gate sits **before** any executor tokens are spent; reject costs only the
  planner call.
- Per-node token usage is accumulated in state (planner/executor/verifier/total),
  pushed live over WebSocket, and attached to every span.

## HITL gates

All three use LangGraph `interrupt()` + `Command`, are bypassed by per-tenant
`auto_approve` policy (so thousands of concurrent runs don't queue on humans), and
append a `HumanDecision {gate, action, actor, timestamp}` to `approval_history` — the
audit trail rendered in the UI:

| gate | surfaces | actions |
|---|---|---|
| `plan_gate` | JSON plan (editable table) | approve · edit · reject |
| `escalation_gate` | last 3 structured errors | retry-with-guidance (×1) · abort · accept-partial |
| `merge_gate` | final diff | approve · reject (rollback) |

## Sandbox & tenant isolation

One ephemeral container per `(tenant, run)`: `--network none`, `--read-only` root +
`--tmpfs /tmp`, `--user 1000:1000`, `--cap-drop ALL`,
`--security-opt no-new-privileges`, memory/CPU limits, tenant repo mounted at
`/workspace/{tenant_id}` from a per-run host copy. Agent A has no mount, route, or
namespace overlapping Agent B — `tests/test_sandbox.py` runs two tenants concurrently
and proves neither can see the other's filesystem or reach it over the network.
Teardown is in `finally`. Pi only ever runs inside this container.

**Documented trade-off:** `--network none` and "Pi calls the LLM API from inside the
container" are mutually exclusive. Verification-only sandboxes run with
`--network none`; a Pi sandbox gets a **dedicated per-run bridge network** (no
cross-container traffic, egress allowed). In prod the equivalent control is the
NetworkPolicy below (egress to HTTPS/DNS only, pod-to-pod denied).

## Telemetry

Every node runs in an OTel span: `duration_ms`, per-node token deltas, verification
`iteration`, `status_after`; gate spans add `gate`, `decision`, `actor`,
`human_wait_ms`. A failed run shows **three executor→verifier span pairs** under one
run span — the loop breakdown, asserted structurally in `tests/test_telemetry.py`
via an in-memory exporter and visible in Jaeger
(service `refactor-harness`; the UI deep-links each run). Tracing is a silent no-op
when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, so offline runs and CI need no collector.

## Deployment brief (Part 2)

**State & resilience.** All run state lives in the checkpointer, not in workers.
Locally that's SQLite; in prod swap `SqliteSaver` for `PostgresSaver` (one line in
`graph.py`) and the API deployment (`infra/k8s/deployment-api.yaml`) becomes
stateless and horizontally scalable — any replica can resume any run after a crash
or deploy, via the same `resume_run` path the tests exercise.

**Telemetry.** OTLP → collector → Jaeger/Tempo. Spans carry tenant, run, iteration,
and token attributes, so per-tenant cost and failure-mode dashboards are queries,
not new code.

**Scaling & tenant isolation.** The local docker-socket spawn is replaced by a
**Kubernetes Job per run** (`infra/k8s/job-template.yaml`): same hardened pod spec
(non-root, read-only rootfs, no capabilities, no SA token, resource limits), one
per-run PVC mounted at a tenant-namespaced path, `ttlSecondsAfterFinished` cleanup.
`infra/k8s/networkpolicy.yaml` denies all executor ingress and all pod-to-pod
traffic, allowing egress only to DNS and HTTPS — Agent A cannot read Agent B's
filesystem (no shared volume, different pods) nor reach it over the network.
Concurrency scales by Job count; the orchestrator only holds a lightweight watch per
active run, and gates park runs at zero cost (state is checkpointed; nothing is
in memory waiting).

## Repository layout

```
orchestrator/            Python 3.12 · LangGraph graph, nodes, gates, adapters,
  src/graph.py           sandbox, telemetry, FastAPI (REST + WS)
  tests/                 39 tests; dryrun path needs no Docker/Pi/key
executor-image/          sandbox image: pi@0.80.3 (pinned, --ignore-scripts) + pytest/ruff
web/                     React + Vite + Tailwind single-page UI (nginx in compose)
infra/                   Dockerfile.orchestrator, k8s Job/Deployment/NetworkPolicy
test-fixtures/sample-repo  vendored debt-demo target (pristine, mounted read-only)
plan.md · CLAUDE.md      the build spec and working agreement
```

## Scope notes

- Terraform: intentionally skipped (plan.md marks it optional; K8s manifests carry
  the prod story).
- Pi RPC token extraction is permissive (harvests any `usage` object in the event
  stream); the documented fallback is one-shot `pi -p --mode json`, which reports
  usage per invocation (`PiAdapter.oneshot_fallback`).
- No auth on the API/UI — out of scope for the demo per the brief.
