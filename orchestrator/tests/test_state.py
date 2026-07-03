"""Phase: state schema. Verifies defaults, constants, and helpers."""
from src.config import MAX_ITERATIONS
from src.state import (
    ErrorRecord,
    HumanDecision,
    PlanStep,
    add_tokens,
    new_state,
    now_iso,
)


def test_max_iterations_is_three():
    assert MAX_ITERATIONS == 3


def test_new_state_defaults():
    s = new_state("t-1", "tenant-a", "/workspace/tenant-a", "/tmp/repo", auto_approve=False)
    assert s["thread_id"] == "t-1"
    assert s["tenant_id"] == "tenant-a"
    assert s["repo_path"] == "/workspace/tenant-a"
    assert s["host_repo_path"] == "/tmp/repo"
    assert s["auto_approve"] is False
    assert s["executor_adapter"] == "dryrun"
    assert s["workspace_kind"] == "local"
    assert s["baseline_failed_tests"] == []
    assert s["baseline_lint_errors"] == []
    assert s["last_verification"] is None
    assert s["human_guidance"] is None
    assert s["plan"] == []
    assert s["current_step"] == 0
    assert s["completed_steps"] == []
    assert s["iteration_count"] == 0
    assert s["escalation_count"] == 0
    assert s["error_log"] == []
    assert s["pending_approval"] is None
    assert s["human_decision"] is None
    assert s["approval_history"] == []
    assert s["token_usage"] == {"planner": 0, "executor": 0, "verifier": 0, "total": 0}
    assert s["status"] == "planning"
    assert s["final_diff"] is None
    assert s["failure_reason"] is None


def test_add_tokens_accumulates_and_does_not_mutate():
    usage = {"planner": 0, "executor": 0, "verifier": 0, "total": 0}
    u2 = add_tokens(usage, "executor", 120)
    u3 = add_tokens(u2, "planner", 30)
    assert usage["total"] == 0  # original untouched
    assert u3 == {"planner": 30, "executor": 120, "verifier": 0, "total": 150}


def test_models_validate():
    step = PlanStep(step_id=0, file="app/db.py", change_type="refactor", rationale="async")
    assert step.status == "pending"
    err = ErrorRecord(step_id=0, iteration=1, stdout="out", stderr="err",
                      failed_tests=["tests/test_api.py::test_x"])
    assert err.timestamp  # auto-filled
    dec = HumanDecision(gate="plan", action="approve", actor="policy:auto_approve")
    assert dec.gate == "plan"


def test_now_iso_is_utc():
    assert "+00:00" in now_iso()
