# CLAUDE.md — Working agreement for building the Refactor Harness

This file governs how you (Claude Code) build this project. Read it fully before starting,
and re-read Section 2 whenever you're about to install a dependency or shell out to Pi.
The full task breakdown lives in `plan.md` — this file is the *how you work*, that file is
the *what to build*.

---

## 1. Prime directive

Build a **LangGraph-orchestrated, Pi-executed, HITL-gated** autonomous refactoring harness
with a React web UI. This is a job assessment scored **80% on the harness** (orchestration,
state machine, verification loops, resilience, sandbox security, telemetry, deployment) and
**20% on the prompt**. Every trade-off favors a robust, observable, resumable harness over a
clever refactor. When unsure, optimize for *"could this run thousands of concurrent runs in
production without losing money or leaking tenant data?"*

---

## 2. Hard rules (do not violate)

1. **Pi package name is `@earendil-works/pi-coding-agent`.** The `@mariozechner/*` package is
   DEPRECATED — never install or reference it. Repo of record: `github.com/earendil-works/pi`.
   Pin an exact version in the Dockerfile (e.g. `@earendil-works/pi-coding-agent@0.80.3`) and
   verify with `pi --version` during the image build.
2. **Pi has no permission system.** It runs with the launching user's full privileges. It MUST
   only ever run inside the locked-down Docker sandbox — never on the orchestrator host, never
   in CI, never against the real repo directly. The container is the security boundary.
3. **Drive Pi headless only.** Use `pi --mode rpc --no-session` (preferred) or
   `pi -p --mode json` (fallback). Non-interactive modes skip the trust prompt — required for
   automation. Never spawn interactive Pi.
4. **RPC framing: split on `\n` ONLY.** Pi's RPC is strict LF-delimited JSONL. Do NOT use
   Python's generic line iteration assumptions or any reader that also splits on U+2028/U+2029
   — those characters occur inside JSON string payloads and will corrupt parsing.
5. **Never weaken the cost guardrail.** `MAX_ITERATIONS = 3` is absolute. The verification loop
   hard-aborts and rolls back at 3. The escalation HITL gate may let a human *override* once,
   but auto-rollback still fires under `auto_approve` or no-response. The guardrail is never a
   hole.
6. **Keep the dryrun path green at all times.** The full graph (loops, gates, crash-resume)
   MUST run end-to-end with the `DryRunAdapter`, no API key, no Pi, no Docker. If a change
   breaks the dryrun tests, fix it before moving on. This is your safety net for a working
   submission.
7. **No secrets in code or git.** API keys come from env only. Provide `.env.example` with
   placeholder keys. Never print full keys in logs.
8. **Never edit files inside read-only mounts** (`test-fixtures/` is the pristine source of
   truth). Copy into a sandbox volume first.

---

## 3. Build order (non-negotiable sequence)

Follow `plan.md` phases in order. The spine is:

```
state → graph → gates → crash-resume  (ALL on DryRunAdapter, zero external deps)
      → sandbox → Pi adapter → verifier loop → telemetry
      → FastAPI (+WS) → React UI → infra → README
```

Get the entire pipeline working on the dryrun adapter and passing `test_crash_resume.py`
BEFORE touching Docker or Pi. Do not build the UI before the API works. Do not write the
README until the system runs.

---

## 4. Definition of Done per phase

Never mark a phase complete without:
1. The phase's **verification command** passing (see `plan.md`).
2. A green run of the dryrun test suite: `cd orchestrator && python -m pytest -q`.
3. A commit with a clear message: `feat(phase-X): <what>` or `test(phase-X): <what>`.

If a verification command can't pass in this environment (e.g. Docker unavailable in CI),
make the test **skip gracefully** with a clear skip reason — never delete it, never fake a
pass.

---

## 5. Coding conventions

**Python (orchestrator)**
- Python 3.12, type hints everywhere, `pydantic` v2 for all external I/O boundaries.
- Pure, testable node functions: each LangGraph node takes `HarnessState`, returns a partial
  update dict (or `Command`). No hidden globals except the sandbox registry, which must be
  rebuildable from persisted state on resume.
- Structured errors, never raw blobs: failures become `ErrorRecord` objects.
- All sandbox interaction goes through the `Sandbox` class — nothing else shells into Docker.
- All Pi interaction goes through `PiAdapter` behind the `ExecutorAdapter` ABC — nothing else
  knows Pi exists. This is what makes the executor swappable (Pi → Claude Code/Codex).
- Format/lint with `ruff`. Keep functions small.

**TypeScript/React (web)**
- Vite + React + TypeScript + Tailwind. Functional components + hooks only.
- One typed API client (`api.ts`) for REST + WebSocket; components never fetch inline.
- No client-side secrets. No router dependency — single page driven by run state.
- Keep it clean and legible; this is a demo surface, not a design contest. Do not gold-plate.

**General**
- Small, frequent commits. Each commit builds and passes dryrun tests.
- Prefer boring, obvious code over cleverness — graders read this.

---

## 6. Testing strategy

- **DryRunAdapter is the workhorse.** It applies a known-correct fix to the fixture and
  reports deterministic token counts, so the whole graph is CI-testable offline.
- Provide adapter variants for loop testing: `FlakyAdapter` (fails N times then succeeds) and
  `AlwaysFailAdapter` (drives the 3-iteration abort + rollback assertion).
- `test_crash_resume.py` MUST assert: (a) run completes after simulated crash, (b) no
  `completed_steps` are re-executed (assert adapter call count), (c) token totals do not
  double. This is the single most important test for the resilience score — do not skip it.
- Docker/Pi integration tests skip cleanly when those aren't present.

---

## 7. Telemetry requirements (don't cut corners here — 20% of grade)

- Every node wrapped in an OTel span with `duration_ms`, per-node token usage, and the current
  verification `iteration`. Gate spans add `gate` and `human_wait_duration`.
- A *failed* run must produce a Jaeger trace that visibly shows THREE executor→verifier span
  pairs — that's the "visual breakdown of verification loop iterations" the brief demands.
- Telemetry has a no-op fallback when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, so offline runs
  and CI still work.

---

## 8. HITL gates (our addition — implement all three)

1. **plan_gate** — approve / edit / reject the JSON plan (before any executor tokens spent).
2. **escalation_gate** — on loop exhaustion: retry-with-guidance / abort / accept-partial.
3. **merge_gate** — review final diff before finalizing / PR.

All gates use LangGraph `interrupt()` + `Command`, are bypassed under `auto_approve` (per-
tenant policy so massive concurrency still works), and append a `HumanDecision` to
`approval_history` (audit trail). The web UI drives all three; the REST API exposes them for
scripted/curl use too.

---

## 9. Security & isolation checklist (verify before README)

- [ ] One ephemeral container per `(tenant, run)`; name-namespaced.
- [ ] `--network none`, `--read-only` root (+ `--tmpfs /tmp`), non-root user, `--cap-drop ALL`,
      `--security-opt no-new-privileges`, memory + cpu limits.
- [ ] Tenant repo mounted at `/workspace/{tenant_id}` — no cross-tenant path overlap.
- [ ] Two concurrent runs demonstrably cannot see each other's filesystem.
- [ ] Pi runs ONLY inside these containers, never on the host.
- [ ] Containers always torn down in a `finally`.
- [ ] Prod story documented: K8s Job-per-run + NetworkPolicy denying pod-to-pod traffic.

---

## 10. What NOT to do

- Don't make Pi the orchestrator or use its extensions to run the pipeline — LangGraph owns
  orchestration; Pi is only the executor. Hiding the graded 80% behind Pi's harness loses the
  assessment.
- Don't over-engineer: no message queue, no distributed DB, no auth system for the demo. The
  brief explicitly rewards solving multi-agent sync "without over-engineering."
- Don't skip the crash-resume test or the loop-abort test to save time.
- Don't invent Pi flags or RPC event shapes — if unsure, consult
  `github.com/earendil-works/pi` docs (`packages/coding-agent/docs/rpc.md`,
  `.../docs/usage.md`, `.../docs/containerization.md`) rather than guessing. If you cannot
  verify a flag, use the documented `pi -p --mode json` fallback and note the assumption in a
  code comment.
- Don't leave TODOs in the submitted code path; either implement or clearly mark as
  out-of-scope in the README.

---

## 11. When you finish

Run the whole thing once from a clean state via `docker compose up`, complete a full run from
the web UI (start → approve plan → watch loop → approve merge → succeeded), confirm the Jaeger
trace shows tokens + loop iterations, then verify the `plan.md` Section 7 "Definition of Done"
checklist end to end. Only then write the README.
