"""Phase H — terminal nodes."""
from pathlib import Path

from ..sandbox.registry import get_workspace
from ..state import HarnessState, coerce_plan


def _pr_body(state: HarnessState, diff: str) -> str:
    plan = coerce_plan(state["plan"])
    done = [s for s in plan if s.status == "done"]
    lines = [
        "## Automated refactor (refactor-harness)",
        "",
        f"- tenant: `{state['tenant_id']}`  run: `{state['thread_id']}`",
        f"- steps completed: {len(done)}/{len(plan)}",
        f"- token usage: {state['token_usage']}",
        "",
        "### Plan",
    ]
    lines += [f"- [{'x' if s.status == 'done' else ' '}] `{s.file}` — {s.rationale}"
              for s in plan]
    if state.get("failure_reason"):
        lines += ["", f"> Note: {state['failure_reason']}"]
    lines += ["", f"<details><summary>diff ({len(diff.splitlines())} lines)</summary>",
              "", "```diff", diff, "```", "</details>"]
    return "\n".join(lines)


def finalizer(state: HarnessState) -> dict:
    """Compute the final diff and emit PR-ready artifacts (.patch + PR body).

    In prod this would open a PR via the GitHub API; the demo writes artifacts
    next to the run workspace so no live auth is needed (plan.md Phase H).
    """
    workspace = get_workspace(state)
    diff = workspace.diff()

    artifacts_dir = Path(state["host_repo_path"]).resolve().parent
    if artifacts_dir.is_dir():
        (artifacts_dir / f"{state['thread_id']}.patch").write_text(diff)
        (artifacts_dir / f"{state['thread_id']}.pr-body.md").write_text(_pr_body(state, diff))

    return {"final_diff": diff, "status": "succeeded", "pending_approval": None}


def aborted(state: HarnessState) -> dict:
    """Single rollback point: every abort path routes here, so the workspace is
    always restored to baseline exactly once."""
    get_workspace(state).rollback()
    return {
        "status": "aborted",
        "failure_reason": state.get("failure_reason") or "aborted",
        "pending_approval": None,
    }
