"""Phase K — FastAPI: REST + WebSocket over the graph.

Design notes:
  * One graph + SqliteSaver per process; each run executes in a worker thread via
    graph.stream() so every node completion is pushed over the WebSocket hub.
  * The run list is DISCOVERED from the checkpoint DB at startup (SELECT DISTINCT
    thread_id), so an API restart loses nothing — same recovery story as the graph.
  * A gate decision and a crash-recovery resume share resume_run(); the API merely
    chooses command=Command(resume=...) or command=None.
"""
import asyncio
import json
import threading
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command

from .. import config
from ..graph import (
    build_graph,
    get_state_values,
    open_checkpointer,
    pending_interrupt,
    thread_config,
)
from ..sandbox.local import prepare_workspace
from ..state import new_state
from ..telemetry.trace import run_span
from .schemas import (
    DecisionRequest,
    PublicState,
    RunSummary,
    StartRunRequest,
    StartRunResponse,
    actions_for_gate,
    to_public,
    to_summary,
)

DEFAULT_TARGET_REPO = Path(__file__).parents[3] / "test-fixtures" / "sample-repo"


class Hub:
    """Thread-safe fan-out of run events to WebSocket subscribers."""

    def __init__(self) -> None:
        self._subs: dict[str, set[WebSocket]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def subscribe(self, thread_id: str, ws: WebSocket) -> None:
        await ws.accept()
        with self._lock:
            self._subs.setdefault(thread_id, set()).add(ws)

    def unsubscribe(self, thread_id: str, ws: WebSocket) -> None:
        with self._lock:
            self._subs.get(thread_id, set()).discard(ws)

    def publish(self, thread_id: str, payload: dict) -> None:
        """Callable from worker threads."""
        if self._loop is None:
            return
        with self._lock:
            targets = list(self._subs.get(thread_id, ()))
        message = json.dumps(payload, default=str)
        for ws in targets:
            asyncio.run_coroutine_threadsafe(self._send(thread_id, ws, message), self._loop)

    async def _send(self, thread_id: str, ws: WebSocket, message: str) -> None:
        try:
            await ws.send_text(message)
        except Exception:
            self.unsubscribe(thread_id, ws)


def create_app(checkpoint_db: str | None = None, runs_root: str | None = None,
               target_repo: str | None = None) -> FastAPI:
    hub = Hub()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.set_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="refactor-harness", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    db_path = checkpoint_db or config.checkpoint_db_path()
    runs_dir = Path(runs_root or config.runs_root()).resolve()
    repo_src = Path(target_repo or DEFAULT_TARGET_REPO).resolve()

    checkpointer = open_checkpointer(db_path)
    graph = build_graph(checkpointer)
    workers: dict[str, threading.Thread] = {}
    workers_lock = threading.Lock()

    # ---------------------------------------------------------------- helpers

    def known_thread_ids() -> list[str]:
        cur = checkpointer.conn.cursor()
        try:
            rows = cur.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
        except Exception:
            return []
        return [r[0] for r in rows]

    def state_or_404(thread_id: str):
        values = get_state_values(graph, thread_id)
        if values is None:
            raise HTTPException(404, f"unknown run: {thread_id}")
        return values

    def push_snapshot(thread_id: str, event: str) -> None:
        values = get_state_values(graph, thread_id)
        if values is None:
            return
        payload = {"type": event, "state": to_public(values).model_dump()}
        pending = pending_interrupt(graph, thread_id)
        if pending is not None:
            payload["pending"] = pending
        hub.publish(thread_id, payload)

    def drive(thread_id: str, first_input) -> None:
        """Worker thread: stream the graph so each node completion pushes state."""
        kind = "start" if isinstance(first_input, dict) else "resume"
        try:
            with run_span(kind, thread_id):
                for _ in graph.stream(first_input, thread_config(thread_id),
                                      stream_mode="updates"):
                    push_snapshot(thread_id, "state")
        except Exception as exc:  # surface infra errors to the UI, keep checkpoint
            hub.publish(thread_id, {"type": "error", "error": str(exc)})
        finally:
            push_snapshot(thread_id, "final")
            with workers_lock:
                workers.pop(thread_id, None)

    def spawn(thread_id: str, first_input) -> None:
        with workers_lock:
            if thread_id in workers:
                raise HTTPException(409, f"run {thread_id} is already executing")
            t = threading.Thread(target=drive, args=(thread_id, first_input),
                                 daemon=True, name=f"run-{thread_id}")
            workers[thread_id] = t
            t.start()

    # ---------------------------------------------------------------- REST

    @app.post("/runs", response_model=StartRunResponse, status_code=201)
    def start(req: StartRunRequest) -> StartRunResponse:
        if req.executor_adapter == "pi" and not config.anthropic_api_key():
            raise HTTPException(400, "executor_adapter=pi requires ANTHROPIC_API_KEY")
        thread_id = f"run-{uuid.uuid4().hex[:12]}"
        host_path = prepare_workspace(repo_src, runs_dir, req.tenant_id, thread_id)
        state = new_state(
            thread_id=thread_id,
            tenant_id=req.tenant_id,
            repo_path=f"/workspace/{req.tenant_id}",
            host_repo_path=str(host_path),
            auto_approve=req.auto_approve,
            executor_adapter=req.executor_adapter,
            workspace_kind="docker" if req.executor_adapter == "pi" else "local",
        )
        spawn(thread_id, state)
        return StartRunResponse(thread_id=thread_id, tenant_id=req.tenant_id,
                                status="planning")

    @app.get("/runs", response_model=list[RunSummary])
    def list_runs() -> list[RunSummary]:
        out = []
        for tid in known_thread_ids():
            values = get_state_values(graph, tid)
            if values is not None:
                out.append(to_summary(values))
        return out

    @app.get("/runs/{thread_id}/state", response_model=PublicState)
    def run_state(thread_id: str) -> PublicState:
        return to_public(state_or_404(thread_id))

    @app.get("/runs/{thread_id}/pending")
    def run_pending(thread_id: str) -> dict:
        state_or_404(thread_id)
        return {"pending": pending_interrupt(graph, thread_id)}

    @app.post("/runs/{thread_id}/decision")
    def decide(thread_id: str, req: DecisionRequest) -> dict:
        state_or_404(thread_id)
        pending = pending_interrupt(graph, thread_id)
        if pending is None:
            raise HTTPException(409, "run has no pending gate")
        gate = pending.get("gate", "")
        if req.action not in actions_for_gate(gate):
            raise HTTPException(422,
                                f"action {req.action!r} invalid for gate {gate!r}; "
                                f"allowed: {sorted(actions_for_gate(gate))}")
        decision = {"action": req.action, "actor": req.actor}
        if req.plan is not None:
            decision["plan"] = req.plan
        if req.guidance:
            decision["guidance"] = req.guidance
        if req.reason:
            decision["reason"] = req.reason
        spawn(thread_id, Command(resume=decision))
        return {"resumed": True, "gate": gate, "action": req.action}

    @app.post("/runs/{thread_id}/resume")
    def recover(thread_id: str) -> dict:
        """Crash recovery: continue from the last checkpoint (command=None).
        The SAME call path as a gate decision — the resilience story."""
        values = state_or_404(thread_id)
        if values["status"] in ("succeeded", "aborted", "failed"):
            raise HTTPException(409, f"run already terminal: {values['status']}")
        if pending_interrupt(graph, thread_id) is not None:
            return {"resumed": False, "reason": "parked at a gate; POST a decision instead"}
        spawn(thread_id, None)
        return {"resumed": True}

    @app.get("/runs/{thread_id}/diff")
    def run_diff(thread_id: str) -> dict:
        values = state_or_404(thread_id)
        return {"diff": values.get("final_diff") or ""}

    @app.get("/runs/{thread_id}/trace")
    def run_trace(thread_id: str) -> dict:
        base = (config.otel_endpoint() and
                config.jaeger_ui_url()) or config.jaeger_ui_url()
        tags = urllib.parse.quote(json.dumps({"harness.thread_id": thread_id}))
        return {"url": f"{base}/search?service=refactor-harness&tags={tags}",
                "exporting": bool(config.otel_endpoint())}

    # ---------------------------------------------------------------- WebSocket

    @app.websocket("/ws/runs/{thread_id}")
    async def ws_run(ws: WebSocket, thread_id: str) -> None:
        await hub.subscribe(thread_id, ws)
        # initial snapshot so late subscribers render immediately
        values = get_state_values(graph, thread_id)
        if values is not None:
            snapshot = {"type": "state", "state": to_public(values).model_dump()}
            pending = pending_interrupt(graph, thread_id)
            if pending is not None:
                snapshot["pending"] = pending
            await ws.send_text(json.dumps(snapshot, default=str))
        try:
            while True:
                await ws.receive_text()  # keepalive/no-op; server only pushes
        except WebSocketDisconnect:
            hub.unsubscribe(thread_id, ws)

    return app


app = create_app()
