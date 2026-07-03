"""Stage 2 — Executor. Applies plan[current_step] via the selected ExecutorAdapter.

Adapter failures (Pi crash, RPC timeout, docker exec error) are infrastructure
failures, not verification failures: they become a structured ErrorRecord and route
straight to the escalation gate — where auto_approve policy aborts + rolls back, or
a human can grant the single retry. Never a raw traceback blob into state.
"""
from typing import Literal

from langgraph.types import Command

from ..executor_adapters import get_adapter
from ..sandbox.registry import get_workspace
from ..state import ErrorRecord, HarnessState, add_tokens, coerce_errors, coerce_plan


def executor(state: HarnessState) -> Command[Literal["verifier", "escalation_gate"]]:
    plan = coerce_plan(state["plan"])
    step = plan[state["current_step"]]

    errors = coerce_errors(state["error_log"])
    prior_error = next((e for e in reversed(errors) if e.step_id == step.step_id), None)

    workspace = get_workspace(state)
    adapter = get_adapter(state["executor_adapter"])
    try:
        result = adapter.apply_step(workspace, step, prior_error,
                                    guidance=state.get("human_guidance"))
    except Exception as exc:  # noqa: BLE001 — anything the adapter throws is infra
        record = ErrorRecord(
            step_id=step.step_id,
            iteration=state["iteration_count"] + 1,
            stdout="",
            stderr=f"executor adapter failure ({type(exc).__name__}): {exc}",
        )
        plan[state["current_step"]] = step.model_copy(update={"status": "failed"})
        return Command(goto="escalation_gate", update={
            "plan": plan,
            "error_log": state["error_log"] + [record],
            "iteration_count": state["iteration_count"] + 1,
            "pending_approval": "escalation",
            "status": "awaiting_human",
        })

    plan[state["current_step"]] = step.model_copy(update={"status": "in_progress"})
    return Command(goto="verifier", update={
        "plan": plan,
        "token_usage": add_tokens(state["token_usage"], "executor", result.tokens),
        "status": "verifying",
    })
