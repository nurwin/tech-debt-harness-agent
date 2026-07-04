"""Process-global workspace registry.

The only sanctioned global (CLAUDE.md §5): keyed by thread_id and REBUILDABLE from
persisted state alone — after a crash, get_workspace(state) reconstructs the handle
from (tenant_id, thread_id, host_repo_path, workspace_kind); the on-disk workspace
and its git baseline survive the process.
"""
from .. import config
from ..state import HarnessState
from .base import Workspace
from .local import LocalWorkspace

_WORKSPACES: dict[str, Workspace] = {}


def get_workspace(state: HarnessState) -> Workspace:
    tid = state["thread_id"]
    ws = _WORKSPACES.get(tid)
    if ws is None:
        if state.get("workspace_kind", "local") == "docker":
            from .sandbox import Sandbox  # deferred: dryrun path never imports docker code

            # Only a Pi sandbox gets LLM egress + credentials; verification-only
            # docker workspaces (e.g. imported repos on dryrun adapters) stay
            # --network none with no secrets inside.
            is_pi = state.get("executor_adapter") == "pi"
            ws = Sandbox(
                state["tenant_id"], tid, state["host_repo_path"],
                allow_egress=is_pi,
                anthropic_api_key=config.anthropic_api_key() if is_pi else None,
                anthropic_base_url=config.anthropic_base_url() if is_pi else None,
            )
            ws.start()
        else:
            ws = LocalWorkspace(state["tenant_id"], tid, state["host_repo_path"])
        _WORKSPACES[tid] = ws
    return ws


def drop_workspace(thread_id: str) -> None:
    ws = _WORKSPACES.pop(thread_id, None)
    if ws is not None:
        ws.teardown()


def reset_workspaces() -> None:
    """Test/crash-simulation helper: forget all handles (does NOT tear down)."""
    _WORKSPACES.clear()
