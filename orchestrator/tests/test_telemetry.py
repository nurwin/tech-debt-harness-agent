"""Phase J: span content is asserted offline via an in-memory exporter — the
Jaeger DoD ('a failed run visibly shows three executor→verifier span pairs') is
checked here as span structure; the manual Jaeger view is the same data over OTLP.
"""
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.executor_adapters import AlwaysFailAdapter, set_adapter, reset_adapters
from src.graph import build_graph, open_checkpointer, start_run
from src.sandbox.local import prepare_workspace
from src.sandbox.registry import reset_workspaces
from src.state import new_state
from src.telemetry import trace as trace_mod
from tests.conftest import FIXTURE_REPO


@pytest.fixture
def exporter(monkeypatch):
    """Fresh in-memory exporter; tracing config is process-global, so install once."""
    exp = getattr(trace_mod, "_test_exporter", None)
    if exp is None:
        exp = InMemorySpanExporter()
        trace_mod.init_tracing(span_exporter=exp)
        trace_mod._test_exporter = exp
    exp.clear()
    reset_workspaces()
    reset_adapters()
    return exp


def test_failed_run_shows_three_executor_verifier_span_pairs(tmp_path, exporter):
    host = prepare_workspace(FIXTURE_REPO, tmp_path, "tenant-a", "t-spans")
    set_adapter("alwaysfail", AlwaysFailAdapter())
    graph = build_graph(open_checkpointer(str(tmp_path / "ckpt.sqlite")))
    final = start_run(graph, new_state(
        "t-spans", "tenant-a", "/w", host_repo_path=str(host),
        auto_approve=True, executor_adapter="alwaysfail"))
    assert final["status"] == "aborted"

    spans = {s.name: s for s in exporter.get_finished_spans()}
    executor_spans = [s for s in exporter.get_finished_spans() if s.name == "node.executor"]
    verifier_spans = [s for s in exporter.get_finished_spans() if s.name == "node.verifier"]
    # THE loop breakdown: three executor→verifier pairs, iterations 0,1,2 going in
    assert len(executor_spans) == 3 and len(verifier_spans) == 3
    assert [s.attributes["harness.iteration"] for s in executor_spans] == [0, 1, 2]
    assert all(s.attributes["harness.verification.passed"] is False for s in verifier_spans)

    # duration + token attribution present on every node span
    assert all("duration_ms" in s.attributes
               for s in spans.values() if s.name.startswith("node."))
    planner = spans["node.planner"]
    assert planner.attributes["harness.tokens.planner"] == 200
    assert executor_spans[0].attributes["harness.tokens.executor"] == 340

    # gate span: escalation with the auto_approve abort decision audited
    esc = spans["node.escalation_gate"]
    assert esc.attributes["harness.gate"] == "escalation"
    assert esc.attributes["harness.decision"] == "abort"
    assert esc.attributes["harness.actor"] == "policy:auto_approve"
    assert "harness.human_wait_ms" in esc.attributes

    # everything hangs under one root run span
    root = spans["run.start"]
    assert root.parent is None
    assert all(s.parent is not None for name, s in spans.items() if name != "run.start")


def test_tracing_is_noop_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    # init only ever configures a real provider when an exporter/endpoint exists;
    # a second call with nothing set must not blow up and must report inactive
    # (the process-global _configured flag may already be True from other tests).
    import importlib

    fresh = importlib.reload(trace_mod)
    try:
        assert fresh.init_tracing() is False  # no endpoint → stays no-op
        with fresh.run_span("start", "t-noop"):
            pass  # must not raise
    finally:
        importlib.reload(trace_mod)  # restore module state for other tests
