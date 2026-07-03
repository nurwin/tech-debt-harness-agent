"""Phases D–I: full-graph runs on the dryrun adapters — loops, all three gates,
hard abort at MAX_ITERATIONS=3 with rollback. No API key, no Pi, no Docker.
"""
import pytest
from langgraph.types import Command

from src.executor_adapters import (
    AlwaysFailAdapter,
    DryRunAdapter,
    FlakyAdapter,
    reset_adapters,
    set_adapter,
)
from src.graph import (
    build_graph,
    open_checkpointer,
    pending_interrupt,
    resume_run,
    start_run,
)
from src.sandbox.local import LocalWorkspace, prepare_workspace
from src.sandbox.registry import reset_workspaces
from src.state import new_state
from tests.conftest import FIXTURE_REPO

# Deterministic token accounting (see dryrun_adapter / planner):
PLANNER_TOKENS = 200
DB_TOKENS, API_TOKENS = 340, 560


class Env:
    """One isolated run environment: workspace copy + sqlite-checkpointed graph."""

    def __init__(self, tmp_path, thread_id="run-1", tenant="tenant-a"):
        self.thread_id = thread_id
        self.host_path = prepare_workspace(FIXTURE_REPO, tmp_path / "runs", tenant, thread_id)
        self.db_path = str(tmp_path / "ckpt.sqlite")
        self.graph = build_graph(open_checkpointer(self.db_path))
        self.tenant = tenant

    def state(self, **kw):
        return new_state(self.thread_id, self.tenant, f"/workspace/{self.tenant}",
                         host_repo_path=str(self.host_path), **kw)

    def workspace(self) -> LocalWorkspace:
        return LocalWorkspace(self.tenant, self.thread_id, self.host_path)


@pytest.fixture
def env(tmp_path):
    reset_workspaces()
    reset_adapters()
    return Env(tmp_path)


# ---------------------------------------------------------------- happy path


def test_full_run_auto_approve_succeeds(env):
    adapter = DryRunAdapter()
    set_adapter("dryrun", adapter)
    final = start_run(env.graph, env.state(auto_approve=True))

    assert final["status"] == "succeeded"
    assert final["completed_steps"] == [0, 1]
    assert [s.file for s in final["plan"]] == ["app/db.py", "app/api.py"]  # leaf-first
    assert adapter.calls == [0, 1]
    assert final["token_usage"] == {
        "planner": PLANNER_TOKENS, "executor": DB_TOKENS + API_TOKENS,
        "verifier": 0, "total": PLANNER_TOKENS + DB_TOKENS + API_TOKENS,
    }
    # both bypassed gates audited
    assert [(d.gate, d.action, d.actor) for d in final["approval_history"]] == [
        ("plan", "approve", "policy:auto_approve"),
        ("merge", "approve", "policy:auto_approve"),
    ]
    assert "UserCreate" in final["final_diff"]
    tests = env.workspace().run_tests()
    assert "9 passed" in tests.stdout


def test_baseline_captured(env):
    set_adapter("dryrun", DryRunAdapter())
    final = start_run(env.graph, env.state(auto_approve=True))
    assert len(final["baseline_failed_tests"]) == 6
    assert any("F401" in e for e in final["baseline_lint_errors"])


# ---------------------------------------------------------------- plan gate


def test_plan_gate_pause_approve_then_merge_approve(env):
    set_adapter("dryrun", DryRunAdapter())
    start_run(env.graph, env.state(auto_approve=False))

    intr = pending_interrupt(env.graph, env.thread_id)
    assert intr["gate"] == "plan"
    assert [s["file"] for s in intr["plan"]] == ["app/db.py", "app/api.py"]

    resume_run(env.graph, env.thread_id,
               Command(resume={"action": "approve", "actor": "human:reviewer"}))
    intr = pending_interrupt(env.graph, env.thread_id)
    assert intr["gate"] == "merge"
    assert "UserCreate" in intr["diff"]

    final = resume_run(env.graph, env.thread_id,
                       Command(resume={"action": "approve", "actor": "human:reviewer"}))
    assert final["status"] == "succeeded"
    assert [(d.gate, d.actor) for d in final["approval_history"]] == [
        ("plan", "human:reviewer"), ("merge", "human:reviewer"),
    ]


def test_plan_gate_edit_replaces_plan(env):
    set_adapter("dryrun", DryRunAdapter())
    start_run(env.graph, env.state(auto_approve=False))
    intr = pending_interrupt(env.graph, env.thread_id)
    edited = [dict(s, rationale="EDITED: " + s["rationale"]) for s in intr["plan"]]

    resume_run(env.graph, env.thread_id,
               Command(resume={"action": "edit", "plan": edited, "actor": "human:reviewer"}))
    final = resume_run(env.graph, env.thread_id,
                       Command(resume={"action": "approve", "actor": "human:reviewer"}))
    assert final["status"] == "succeeded"
    assert all(s.rationale.startswith("EDITED:") for s in final["plan"])
    assert final["approval_history"][0].action == "edit"


def test_plan_gate_reject_aborts_before_any_executor_tokens(env):
    adapter = DryRunAdapter()
    set_adapter("dryrun", adapter)
    start_run(env.graph, env.state(auto_approve=False))
    final = resume_run(
        env.graph, env.thread_id,
        Command(resume={"action": "reject", "actor": "human:reviewer", "reason": "nope"}))
    assert final["status"] == "aborted"
    assert final["failure_reason"] == "nope"
    assert adapter.calls == []  # no executor tokens spent
    assert final["token_usage"]["executor"] == 0
    assert env.workspace().diff().strip() == ""


# ---------------------------------------------------------------- verification loop


def test_flaky_adapter_self_corrects(env):
    adapter = FlakyAdapter(fail_times=2)
    set_adapter("flaky", adapter)
    final = start_run(env.graph, env.state(auto_approve=True, executor_adapter="flaky"))

    assert final["status"] == "succeeded"
    # 3 attempts per step (2 broken + 1 good), never reaching the guardrail
    assert adapter.calls == [0, 0, 0, 1, 1, 1]
    assert [(e.step_id, e.iteration) for e in final["error_log"]] == [
        (0, 1), (0, 2), (1, 1), (1, 2),
    ]
    assert final["escalation_count"] == 0
    assert final["token_usage"]["executor"] == 3 * DB_TOKENS + 3 * API_TOKENS
    assert "9 passed" in env.workspace().run_tests().stdout


def test_always_fail_hard_aborts_at_three_and_rolls_back(env):
    adapter = AlwaysFailAdapter()
    set_adapter("alwaysfail", adapter)
    final = start_run(env.graph, env.state(auto_approve=True, executor_adapter="alwaysfail"))

    assert final["status"] == "aborted"
    assert adapter.calls == [0, 0, 0]  # exactly MAX_ITERATIONS attempts, step 0 only
    assert final["iteration_count"] == 3
    assert len(final["error_log"]) == 3
    # auto_approve escalation decision is ABORT (guardrail is never weakened)
    esc = [d for d in final["approval_history"] if d.gate == "escalation"]
    assert len(esc) == 1 and esc[0].action == "abort" and esc[0].actor == "policy:auto_approve"
    assert env.workspace().diff().strip() == ""  # rolled back to baseline


# ---------------------------------------------------------------- escalation gate


def test_escalation_retry_with_guidance_recovers(env):
    adapter = FlakyAdapter(fail_times=3, only_step=0)  # step 0 exhausts the loop once
    set_adapter("flaky", adapter)
    start_run(env.graph, env.state(auto_approve=False, executor_adapter="flaky"))
    resume_run(env.graph, env.thread_id, Command(resume={"action": "approve"}))  # plan gate

    intr = pending_interrupt(env.graph, env.thread_id)
    assert intr["gate"] == "escalation"
    assert intr["step"]["file"] == "app/db.py"
    assert len(intr["errors"]) == 3

    resume_run(env.graph, env.thread_id, Command(resume={
        "action": "retry", "actor": "human:reviewer", "guidance": "drop the unused import"}))
    final = resume_run(env.graph, env.thread_id, Command(resume={"action": "approve"}))
    assert final["status"] == "succeeded"
    assert final["escalation_count"] == 1
    assert adapter.calls == [0, 0, 0, 0, 1]  # 4th attempt on step 0 succeeded


def test_escalation_retry_budget_is_absolute(env):
    adapter = FlakyAdapter(fail_times=99, only_step=0)  # never recovers
    set_adapter("flaky", adapter)
    start_run(env.graph, env.state(auto_approve=False, executor_adapter="flaky"))
    resume_run(env.graph, env.thread_id, Command(resume={"action": "approve"}))
    resume_run(env.graph, env.thread_id, Command(resume={"action": "retry"}))  # 1st: allowed
    final = resume_run(env.graph, env.thread_id, Command(resume={"action": "retry"}))  # 2nd: refused
    assert final["status"] == "aborted"
    assert "budget" in final["failure_reason"]
    assert adapter.calls == [0] * 6  # 3 + 3, never a 7th
    assert env.workspace().diff().strip() == ""


def test_escalation_accept_partial_keeps_verified_steps(env):
    adapter = FlakyAdapter(fail_times=99, only_step=1)  # step 0 fine, step 1 hopeless
    set_adapter("flaky", adapter)
    start_run(env.graph, env.state(auto_approve=False, executor_adapter="flaky"))
    resume_run(env.graph, env.thread_id, Command(resume={"action": "approve"}))

    final = resume_run(env.graph, env.thread_id,
                       Command(resume={"action": "accept_partial", "actor": "human:reviewer"}))
    assert final["status"] == "succeeded"
    assert final["completed_steps"] == [0]
    assert "partial" in final["failure_reason"]
    diff = env.workspace().diff()
    assert "async def get_user" in diff  # verified step 0 kept
    assert "UserCreate" not in diff  # failed step 1 attempt discarded


def test_escalation_abort_by_human_rolls_back(env):
    adapter = FlakyAdapter(fail_times=99, only_step=0)
    set_adapter("flaky", adapter)
    start_run(env.graph, env.state(auto_approve=False, executor_adapter="flaky"))
    resume_run(env.graph, env.thread_id, Command(resume={"action": "approve"}))
    final = resume_run(env.graph, env.thread_id,
                       Command(resume={"action": "abort", "actor": "human:reviewer"}))
    assert final["status"] == "aborted"
    assert env.workspace().diff().strip() == ""


# ---------------------------------------------------------------- merge gate


def test_merge_reject_rolls_back(env):
    set_adapter("dryrun", DryRunAdapter())
    start_run(env.graph, env.state(auto_approve=False))
    resume_run(env.graph, env.thread_id, Command(resume={"action": "approve"}))
    final = resume_run(
        env.graph, env.thread_id,
        Command(resume={"action": "reject", "actor": "human:reviewer",
                        "reason": "diff too broad"}))
    assert final["status"] == "aborted"
    assert final["failure_reason"] == "diff too broad"
    assert env.workspace().diff().strip() == ""
    assert "6 failed" in env.workspace().run_tests().stdout  # back to pristine debt
