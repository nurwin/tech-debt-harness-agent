"""The resilience test (plan.md Phase I — the single most important test).

Simulates a mid-run orchestrator crash: stream the graph, kill it after step 0 is
verified, throw away every in-memory object (graph, adapters, workspace handles),
rebuild from the same SQLite file, resume with command=None, and assert:
  (a) the run completes,
  (b) completed steps are NOT re-executed (adapter call counts),
  (c) token totals do not double.
"""
import pytest
from langgraph.types import Command

from src.executor_adapters import DryRunAdapter, reset_adapters, set_adapter
from src.graph import (
    build_graph,
    get_state_values,
    open_checkpointer,
    pending_interrupt,
    resume_run,
    start_run,
)
from src.sandbox.local import prepare_workspace
from src.sandbox.registry import reset_workspaces
from src.state import new_state
from tests.conftest import FIXTURE_REPO

PLANNER_TOKENS, DB_TOKENS, API_TOKENS = 200, 340, 560
EXPECTED_TOTAL = PLANNER_TOKENS + DB_TOKENS + API_TOKENS


@pytest.fixture(autouse=True)
def _clean_registries():
    reset_workspaces()
    reset_adapters()
    yield
    reset_workspaces()
    reset_adapters()


def _crash_after_first_verified_step(graph, state, config_thread_id):
    """Drive the run via stream() and 'kill the server' right after the verifier
    checkpoints step 0 as done — mid-run, between steps."""
    from src.graph import thread_config

    for update in graph.stream(state, thread_config(config_thread_id),
                               stream_mode="updates"):
        if "verifier" in update and update["verifier"].get("completed_steps") == [0]:
            return  # crash: abandon the stream, discard everything in memory


def test_crash_mid_run_resume_completes_without_reexecution_or_token_doubling(tmp_path):
    thread_id = "crash-run"
    host_path = prepare_workspace(FIXTURE_REPO, tmp_path / "runs", "tenant-a", thread_id)
    db_path = str(tmp_path / "ckpt.sqlite")

    # --- process 1: run until the simulated crash -------------------------
    adapter1 = DryRunAdapter()
    set_adapter("dryrun", adapter1)
    graph1 = build_graph(open_checkpointer(db_path))
    state = new_state(thread_id, "tenant-a", "/workspace/tenant-a",
                      host_repo_path=str(host_path), auto_approve=True)
    _crash_after_first_verified_step(graph1, state, thread_id)
    assert adapter1.calls == [0]  # step 0 executed exactly once before the crash

    # --- the crash: drop ALL in-memory state ------------------------------
    del graph1
    reset_workspaces()
    reset_adapters()

    # --- process 2: rebuild from the same SQLite file and resume ----------
    adapter2 = DryRunAdapter()
    set_adapter("dryrun", adapter2)
    graph2 = build_graph(open_checkpointer(db_path))

    resumed = get_state_values(graph2, thread_id)
    assert resumed["completed_steps"] == [0]  # persisted progress survived

    final = resume_run(graph2, thread_id, command=None)  # crash-recovery call path

    # (a) completes
    assert final["status"] == "succeeded"
    assert final["completed_steps"] == [0, 1]
    # (b) step 0 was NOT re-executed after resume
    assert adapter2.calls == [1]
    # (c) token totals did not double
    assert final["token_usage"]["executor"] == DB_TOKENS + API_TOKENS
    assert final["token_usage"]["total"] == EXPECTED_TOTAL


def test_crash_while_parked_at_gate_then_human_decision(tmp_path):
    """A run parked on a gate survives a crash: resume(None) re-raises the same
    interrupt; the human decision then flows through the SAME resume path."""
    thread_id = "gate-crash-run"
    host_path = prepare_workspace(FIXTURE_REPO, tmp_path / "runs", "tenant-a", thread_id)
    db_path = str(tmp_path / "ckpt.sqlite")

    set_adapter("dryrun", DryRunAdapter())
    graph1 = build_graph(open_checkpointer(db_path))
    state = new_state(thread_id, "tenant-a", "/workspace/tenant-a",
                      host_repo_path=str(host_path), auto_approve=False)
    start_run(graph1, state)
    assert pending_interrupt(graph1, thread_id)["gate"] == "plan"

    del graph1
    reset_workspaces()
    reset_adapters()
    adapter2 = DryRunAdapter()
    set_adapter("dryrun", adapter2)
    graph2 = build_graph(open_checkpointer(db_path))

    resume_run(graph2, thread_id, command=None)  # recovery: still parked, no re-planning
    intr = pending_interrupt(graph2, thread_id)
    assert intr["gate"] == "plan"
    assert get_state_values(graph2, thread_id)["token_usage"]["planner"] == PLANNER_TOKENS

    resume_run(graph2, thread_id, Command(resume={"action": "approve"}))
    final = resume_run(graph2, thread_id, Command(resume={"action": "approve"}))
    assert final["status"] == "succeeded"
    # planner ran once across both processes: tokens did not double
    assert final["token_usage"]["planner"] == PLANNER_TOKENS
    assert final["token_usage"]["total"] == EXPECTED_TOTAL
    assert adapter2.calls == [0, 1]
