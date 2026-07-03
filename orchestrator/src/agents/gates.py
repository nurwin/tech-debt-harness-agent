"""Phase G — the three HITL gates. LangGraph interrupt() + Command.

Every gate:
  * is bypassed under auto_approve (per-tenant policy, so massive concurrency works),
  * appends a HumanDecision to approval_history (audit trail),
  * resumes via the SAME resume_run() path as crash recovery.

Guardrail invariant (CLAUDE.md rule 5): the escalation gate can grant at most ONE
human retry; under auto_approve (or any non-retry outcome) rollback fires. HITL is
an override, not a hole in the MAX_ITERATIONS=3 cost cap.
"""
from typing import Literal

from langgraph.types import Command, interrupt

from ..config import MAX_ESCALATIONS, MAX_ITERATIONS
from ..sandbox.registry import get_workspace
from ..state import HarnessState, HumanDecision, PlanStep, coerce_errors, coerce_plan


def _decide(state: HarnessState, gate: str, payload: dict) -> dict:
    """Bypass under auto_approve; otherwise park the run on interrupt()."""
    if state["auto_approve"]:
        # For plan/merge the policy approves; for escalation it must NOT retry —
        # abort keeps the auto-rollback guarantee.
        action = "abort" if gate == "escalation" else "approve"
        return {"action": action, "actor": "policy:auto_approve"}
    decision = interrupt({"gate": gate, "thread_id": state["thread_id"], **payload})
    decision.setdefault("actor", "human")
    return decision


def _audit(state: HarnessState, gate: str, decision: dict) -> dict:
    extra = {k: v for k, v in decision.items() if k not in ("action", "actor")}
    record = HumanDecision(gate=gate, action=decision["action"],
                           actor=decision["actor"], payload=extra or None)
    return {
        "approval_history": state["approval_history"] + [record],
        "human_decision": decision,
        "pending_approval": None,
    }


def plan_gate(state: HarnessState) -> Command[Literal["executor", "aborted"]]:
    plan = coerce_plan(state["plan"])
    decision = _decide(state, "plan", {
        "plan": [s.model_dump() for s in plan],
        "token_usage": state["token_usage"],
    })
    update = _audit(state, "plan", decision)
    action = decision["action"]

    if action == "approve":
        return Command(goto="executor", update={**update, "status": "executing"})
    if action == "edit":
        edited = [
            PlanStep.model_validate({**raw, "step_id": i, "status": "pending"})
            for i, raw in enumerate(decision["plan"])
        ]
        if not edited:
            return Command(goto="aborted",
                           update={**update, "failure_reason": "edited plan was empty"})
        return Command(goto="executor",
                       update={**update, "plan": edited, "current_step": 0,
                               "status": "executing"})
    if action == "reject":
        reason = decision.get("reason", "plan rejected at plan_gate")
        return Command(goto="aborted", update={**update, "failure_reason": reason})
    raise ValueError(f"plan_gate: unknown action {action!r} (expected approve|edit|reject)")


def escalation_gate(
    state: HarnessState,
) -> Command[Literal["executor", "finalizer", "aborted"]]:
    plan = coerce_plan(state["plan"])
    step = plan[state["current_step"]]
    errors = coerce_errors(state["error_log"])
    decision = _decide(state, "escalation", {
        "step": step.model_dump(),
        "errors": [e.model_dump() for e in errors[-MAX_ITERATIONS:]],
        "iteration_count": state["iteration_count"],
        "escalation_count": state["escalation_count"],
        "completed_steps": state["completed_steps"],
        "token_usage": state["token_usage"],
    })
    update = _audit(state, "escalation", decision)
    action = decision["action"]

    if action == "retry":
        if state["escalation_count"] >= MAX_ESCALATIONS:
            # Retry budget exhausted — the guardrail wins over the human request.
            update["human_decision"] = {**decision, "action": "abort",
                                        "forced_by": "escalation_budget"}
            return Command(goto="aborted", update={
                **update,
                "failure_reason": f"step {step.step_id} failed {MAX_ITERATIONS} iterations; "
                                  "escalation retry budget already spent",
            })
        return Command(goto="executor", update={
            **update,
            "iteration_count": 0,
            "escalation_count": state["escalation_count"] + 1,
            "human_guidance": decision.get("guidance"),
            "status": "executing",
        })
    if action == "accept_partial" and state["completed_steps"]:
        # Keep verified step commits, drop the failed attempt's uncommitted edits.
        get_workspace(state).discard_uncommitted()
        return Command(goto="finalizer", update={
            **update,
            "failure_reason": f"partial: kept steps {state['completed_steps']}, "
                              f"abandoned step {step.step_id} ({step.file})",
            "status": "finalizing",
        })
    # abort — or accept_partial with nothing completed, or anything unrecognized
    # under auto_approve. Rollback happens in the aborted node.
    reason = decision.get(
        "reason",
        f"step {step.step_id} ({step.file}) failed {MAX_ITERATIONS} verification "
        f"iterations; aborted by {decision['actor']}",
    )
    return Command(goto="aborted", update={**update, "failure_reason": reason})


def merge_gate(state: HarnessState) -> Command[Literal["finalizer", "aborted"]]:
    diff = get_workspace(state).diff()
    decision = _decide(state, "merge", {
        "diff": diff,
        "plan": [s.model_dump() for s in coerce_plan(state["plan"])],
        "completed_steps": state["completed_steps"],
        "token_usage": state["token_usage"],
    })
    update = _audit(state, "merge", decision)
    action = decision["action"]

    if action == "approve":
        return Command(goto="finalizer",
                       update={**update, "final_diff": diff, "status": "finalizing"})
    if action == "reject":
        reason = decision.get("reason", "final diff rejected at merge_gate")
        return Command(goto="aborted", update={**update, "failure_reason": reason})
    raise ValueError(f"merge_gate: unknown action {action!r} (expected approve|reject)")
