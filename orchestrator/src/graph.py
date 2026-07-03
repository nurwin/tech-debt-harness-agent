"""Phase I — graph assembly + checkpointing.

    START → planner → plan_gate ─(approve/edit)→ executor → verifier
                         │(reject)                  ▲   │
                         ▼                          │   ├─ pass, more steps ──→ executor
                      aborted ← escalation_gate ←───┘   ├─ pass, exhausted ───→ merge_gate
                         ▲       │(retry×1)  │(partial) ├─ fail, iter < 3 ────→ executor
                         │       └→ executor └→ finalizer└─ fail, iter ≥ 3 ───→ escalation_gate
                         └──(merge reject)── merge_gate ─(approve)→ finalizer → END

resume_run() serves BOTH crash recovery (command=None: re-enter the last
checkpoint, re-raising any pending interrupt) AND human gate decisions
(command=Command(resume=decision)). One code path, which is the resilience story:
a dead worker and a slow human are the same problem to the graph.
"""
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .agents.executor import executor
from .agents.gates import escalation_gate, merge_gate, plan_gate
from .agents.planner import planner
from .agents.terminal import aborted, finalizer
from .agents.verifier import route_after_verifier, verifier
from .state import HarnessState

# Generous ceiling: worst case (2 steps × 3 iterations × 2 nodes, + gates + retry)
# is ~25 node executions; the guardrail that actually bounds cost is MAX_ITERATIONS.
RECURSION_LIMIT = 100


def build_graph(checkpointer: SqliteSaver | None = None):
    g = StateGraph(HarnessState)
    g.add_node("planner", planner)
    g.add_node("plan_gate", plan_gate)
    g.add_node("executor", executor)
    g.add_node("verifier", verifier)
    g.add_node("escalation_gate", escalation_gate)
    g.add_node("merge_gate", merge_gate)
    g.add_node("finalizer", finalizer)
    g.add_node("aborted", aborted)

    g.add_edge(START, "planner")
    g.add_edge("planner", "plan_gate")
    g.add_edge("executor", "verifier")
    g.add_conditional_edges("verifier", route_after_verifier,
                            ["executor", "merge_gate", "escalation_gate"])
    g.add_edge("finalizer", END)
    g.add_edge("aborted", END)
    return g.compile(checkpointer=checkpointer)


def open_checkpointer(db_path: str) -> SqliteSaver:
    Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


def start_run(graph, state: HarnessState) -> dict:
    """Invoke a fresh run to its first interrupt or END."""
    return graph.invoke(state, thread_config(state["thread_id"]))


def resume_run(graph, thread_id: str, command: Command | None = None) -> dict:
    """Continue from the last checkpoint. command=None → crash recovery;
    command=Command(resume=decision) → human gate decision."""
    return graph.invoke(command, thread_config(thread_id))


def get_state_values(graph, thread_id: str) -> dict[str, Any] | None:
    snapshot = graph.get_state(thread_config(thread_id))
    return dict(snapshot.values) if snapshot and snapshot.values else None


def pending_interrupt(graph, thread_id: str) -> dict[str, Any] | None:
    """The payload the run is parked on (a gate's interrupt), if any."""
    snapshot = graph.get_state(thread_config(thread_id))
    if not snapshot:
        return None
    for task in snapshot.tasks:
        for intr in task.interrupts:
            return intr.value
    return None
