"""HarnessState — the single shared state threaded through every LangGraph node.

Checkpointed by SqliteSaver after every transition, so a crashed run resumes from
the last completed node with no re-execution and no duplicated LLM spend.

Nodes return *partial update dicts*; lists are replaced whole (state["x"] + [item])
rather than via reducers — one node runs at a time, and explicit replacement is
easier to audit than merge semantics.
"""
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

Status = Literal[
    "planning",
    "awaiting_human",
    "executing",
    "verifying",
    "finalizing",
    "succeeded",
    "failed",
    "aborted",
]

GateName = Literal["plan", "escalation", "merge"]

StepStatus = Literal["pending", "in_progress", "done", "failed"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanStep(BaseModel):
    step_id: int
    file: str
    change_type: str
    rationale: str
    status: StepStatus = "pending"


class ErrorRecord(BaseModel):
    """Structured verification failure — never a raw blob (CLAUDE.md §5)."""

    step_id: int
    iteration: int
    stdout: str
    stderr: str
    failed_tests: list[str] = Field(default_factory=list)
    lint_errors: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=now_iso)


class HumanDecision(BaseModel):
    """Audit-trail entry for every gate decision (human or auto_approve policy)."""

    gate: GateName
    action: str
    actor: str  # e.g. "human:<name>", "policy:auto_approve", "policy:timeout"
    timestamp: str = Field(default_factory=now_iso)
    payload: dict[str, Any] | None = None


class HarnessState(TypedDict):
    # Identity
    thread_id: str
    tenant_id: str
    repo_path: str  # path INSIDE the sandbox, e.g. /workspace/{tenant_id}
    host_repo_path: str  # host dir mounted into the sandbox; needed to rebuild it on resume
    auto_approve: bool

    # Planning
    plan: list[PlanStep]
    current_step: int
    completed_steps: list[int]

    # Verification loop
    iteration_count: int
    escalation_count: int
    error_log: list[ErrorRecord]

    # HITL
    pending_approval: GateName | None
    human_decision: dict[str, Any] | None
    approval_history: list[HumanDecision]

    # Cost
    token_usage: dict[str, int]  # {planner, executor, verifier, total}

    # Status / output
    status: Status
    final_diff: str | None
    failure_reason: str | None


def new_state(
    thread_id: str,
    tenant_id: str,
    repo_path: str,
    host_repo_path: str = "",
    auto_approve: bool = False,
) -> HarnessState:
    return HarnessState(
        thread_id=thread_id,
        tenant_id=tenant_id,
        repo_path=repo_path,
        host_repo_path=host_repo_path,
        auto_approve=auto_approve,
        plan=[],
        current_step=0,
        completed_steps=[],
        iteration_count=0,
        escalation_count=0,
        error_log=[],
        pending_approval=None,
        human_decision=None,
        approval_history=[],
        token_usage={"planner": 0, "executor": 0, "verifier": 0, "total": 0},
        status="planning",
        final_diff=None,
        failure_reason=None,
    )


def add_tokens(usage: dict[str, int], node: str, count: int) -> dict[str, int]:
    """Return a new token_usage dict with `count` added to `node` and `total`."""
    updated = dict(usage)
    updated[node] = updated.get(node, 0) + count
    updated["total"] = updated.get("total", 0) + count
    return updated
