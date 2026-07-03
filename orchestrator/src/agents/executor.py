"""Stage 2 — Executor. Applies plan[current_step] via the selected ExecutorAdapter."""
from ..executor_adapters import get_adapter
from ..sandbox.registry import get_workspace
from ..state import HarnessState, add_tokens, coerce_errors, coerce_plan


def executor(state: HarnessState) -> dict:
    plan = coerce_plan(state["plan"])
    step = plan[state["current_step"]]

    errors = coerce_errors(state["error_log"])
    prior_error = next((e for e in reversed(errors) if e.step_id == step.step_id), None)

    workspace = get_workspace(state)
    adapter = get_adapter(state["executor_adapter"])
    result = adapter.apply_step(workspace, step, prior_error)

    plan[state["current_step"]] = step.model_copy(update={"status": "in_progress"})
    return {
        "plan": plan,
        "token_usage": add_tokens(state["token_usage"], "executor", result.tokens),
        "status": "verifying",
    }
