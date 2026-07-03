"""Process-global workspace registry.

The only sanctioned global (CLAUDE.md §5): keyed by thread_id and REBUILDABLE from
persisted state alone — after a crash, get_workspace(state) reconstructs the handle
from (tenant_id, thread_id, host_repo_path, workspace_kind); the on-disk workspace
and its git baseline survive the process.
"""
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

            ws = Sandbox(state["tenant_id"], tid, state["host_repo_path"])
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
