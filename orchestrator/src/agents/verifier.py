"""Stage 3 — Verifier + loop router.

Runs tests + lint in the workspace on EVERY iteration and records structured
results. Pass criteria are two-tier (rationale in state.py):
  * final step:        zero test failures AND zero lint errors — the contract.
  * intermediate step: no NEW lint errors vs the run baseline (a cross-file-atomic
    refactor cannot be test-green mid-plan; test results are still recorded and
    fed back to the executor on retry).

The router enforces the hard cost guardrail: 3 failed iterations on a step routes
to the escalation gate, where auto-rollback fires unless a human overrides once.
"""
import re

from ..config import MAX_ITERATIONS
from ..sandbox.registry import get_workspace
from ..state import ErrorRecord, HarnessState, coerce_plan

_LINT_LINE = re.compile(r"^\S+?:\d+:\d+: \w+", re.MULTILINE)
_TAIL = 4000  # keep checkpoints slim: ErrorRecords carry output tails, not blobs


def parse_failed_tests(pytest_stdout: str) -> list[str]:
    return [
        line.removeprefix("FAILED ").split(" - ")[0].strip()
        for line in pytest_stdout.splitlines()
        if line.startswith(("FAILED ", "ERROR "))
    ]


def parse_lint_errors(ruff_stdout: str) -> list[str]:
    return _LINT_LINE.findall(ruff_stdout)


def _tail(s: str) -> str:
    return s[-_TAIL:]


def verifier(state: HarnessState) -> dict:
    workspace = get_workspace(state)
    plan = coerce_plan(state["plan"])
    step = plan[state["current_step"]]
    is_final = state["current_step"] == len(plan) - 1

    tests = workspace.run_tests()
    lint = workspace.run_lint()
    failed_tests = parse_failed_tests(tests.stdout)
    lint_errors = parse_lint_errors(lint.stdout)
    new_lint_errors = sorted(set(lint_errors) - set(state["baseline_lint_errors"]))

    if is_final:
        passed = not failed_tests and not lint_errors and tests.ok and lint.ok
    else:
        passed = not new_lint_errors

    verification = {
        "passed": passed,
        "is_final": is_final,
        "step_id": step.step_id,
        "iteration": state["iteration_count"] + (0 if passed else 1),
        "failed_tests": failed_tests,
        "lint_errors": lint_errors,
        "new_lint_errors": new_lint_errors,
    }

    if passed:
        plan[state["current_step"]] = step.model_copy(update={"status": "done"})
        workspace.commit(f"harness: step {step.step_id} verified ({step.file})")
        more = state["current_step"] + 1 < len(plan)
        return {
            "plan": plan,
            "completed_steps": state["completed_steps"] + [step.step_id],
            "current_step": state["current_step"] + 1,
            "iteration_count": 0,
            "human_guidance": None,  # consumed by the successful attempt
            "last_verification": verification,
            "status": "executing" if more else "finalizing",
        }

    record = ErrorRecord(
        step_id=step.step_id,
        iteration=state["iteration_count"] + 1,
        stdout=_tail(tests.stdout),
        stderr=_tail(tests.stderr or lint.stderr),
        failed_tests=failed_tests,
        lint_errors=new_lint_errors if not is_final else lint_errors,
    )
    plan[state["current_step"]] = step.model_copy(update={"status": "failed"})
    return {
        "plan": plan,
        "error_log": state["error_log"] + [record],
        "iteration_count": state["iteration_count"] + 1,
        "last_verification": verification,
        "status": "executing",
    }


def route_after_verifier(state: HarnessState) -> str:
    verification = state["last_verification"] or {}
    if verification.get("passed"):
        return "executor" if state["current_step"] < len(state["plan"]) else "merge_gate"
    if state["iteration_count"] >= MAX_ITERATIONS:
        return "escalation_gate"  # hard guardrail — never bypassed
    return "executor"
