"""Pydantic models for every REST/WS boundary (CLAUDE.md §5 — typed external I/O)."""
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..repo_import import validate_github_url
from ..state import HarnessState, coerce_errors, coerce_plan


class StartRunRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    auto_approve: bool = False
    executor_adapter: Literal["dryrun", "flaky", "alwaysfail", "pi"] = "dryrun"
    # Optional: import the target from a public GitHub repo instead of the
    # configured fixture. Canonicalized here so a bad URL is a 422, not a clone.
    repo_url: str | None = None

    @field_validator("repo_url")
    @classmethod
    def _github_public_only(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return validate_github_url(value)


class StartRunResponse(BaseModel):
    thread_id: str
    tenant_id: str
    status: str


class DecisionRequest(BaseModel):
    """One gate decision. Allowed actions depend on the pending gate:
    plan: approve|edit|reject · escalation: retry|abort|accept_partial · merge: approve|reject
    """
    action: Literal["approve", "edit", "reject", "retry", "abort", "accept_partial"]
    actor: str = "human:web-ui"
    plan: list[dict[str, Any]] | None = None  # for plan edit
    guidance: str | None = None  # for escalation retry
    reason: str | None = None  # for reject/abort


_GATE_ACTIONS = {
    "plan": {"approve", "edit", "reject"},
    "escalation": {"retry", "abort", "accept_partial"},
    "merge": {"approve", "reject"},
}


def actions_for_gate(gate: str) -> set[str]:
    return _GATE_ACTIONS.get(gate, set())


class RunSummary(BaseModel):
    thread_id: str
    tenant_id: str
    status: str
    pending_approval: str | None
    current_step: int
    plan_length: int
    completed_steps: list[int]
    iteration_count: int
    token_total: int
    failure_reason: str | None = None


class PublicState(BaseModel):
    """The full run state as the UI sees it (final_diff served separately)."""
    thread_id: str
    tenant_id: str
    status: str
    auto_approve: bool
    executor_adapter: str
    plan: list[dict[str, Any]]
    current_step: int
    completed_steps: list[int]
    iteration_count: int
    escalation_count: int
    error_log: list[dict[str, Any]]
    baseline_failed_tests: list[str]
    baseline_lint_errors: list[str]
    last_verification: dict[str, Any] | None
    pending_approval: str | None
    approval_history: list[dict[str, Any]]
    token_usage: dict[str, int]
    failure_reason: str | None
    has_final_diff: bool
    source_repo_url: str | None = None


def to_public(state: HarnessState) -> PublicState:
    return PublicState(
        thread_id=state["thread_id"],
        tenant_id=state["tenant_id"],
        status=state["status"],
        auto_approve=state["auto_approve"],
        executor_adapter=state["executor_adapter"],
        # .get: runs checkpointed before this field existed lack the key
        source_repo_url=state.get("source_repo_url"),
        plan=[s.model_dump() for s in coerce_plan(state["plan"])],
        current_step=state["current_step"],
        completed_steps=state["completed_steps"],
        iteration_count=state["iteration_count"],
        escalation_count=state["escalation_count"],
        error_log=[e.model_dump() for e in coerce_errors(state["error_log"])],
        baseline_failed_tests=state["baseline_failed_tests"],
        baseline_lint_errors=state["baseline_lint_errors"],
        last_verification=state["last_verification"],
        pending_approval=state["pending_approval"],
        approval_history=[
            d if isinstance(d, dict) else d.model_dump() for d in state["approval_history"]
        ],
        token_usage=state["token_usage"],
        failure_reason=state["failure_reason"],
        has_final_diff=bool(state["final_diff"]),
    )


def to_summary(state: HarnessState) -> RunSummary:
    return RunSummary(
        thread_id=state["thread_id"],
        tenant_id=state["tenant_id"],
        status=state["status"],
        pending_approval=state["pending_approval"],
        current_step=state["current_step"],
        plan_length=len(state["plan"]),
        completed_steps=state["completed_steps"],
        iteration_count=state["iteration_count"],
        token_total=state["token_usage"].get("total", 0),
        failure_reason=state["failure_reason"],
    )
