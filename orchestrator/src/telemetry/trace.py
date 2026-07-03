"""Phase J — OTel spans → Jaeger (OTLP HTTP), with a no-op fallback.

Every graph node is wrapped in a span carrying: duration_ms, the current
verification iteration, per-node token deltas, and (on gate spans) the gate name,
decision, and human_wait_ms. A failed run therefore shows the three
executor→verifier span pairs the brief demands, as siblings under one run span.

When OTEL_EXPORTER_OTLP_ENDPOINT is unset, opentelemetry's default Proxy/NoOp
tracer is used and nothing is exported — offline runs and CI never need a
collector.
"""
import time
from contextlib import contextmanager
from typing import Any, Callable

from langgraph.errors import GraphInterrupt
from opentelemetry import trace as otel
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from .. import config

SERVICE_NAME = "refactor-harness"

_configured = False

# Parked-gate bookkeeping for human_wait_ms. Best-effort: survives within a
# process; after a crash the wait clock restarts (the audit trail's timestamps
# remain the durable record).
_PARKED_AT: dict[tuple[str, str], float] = {}


def init_tracing(span_exporter=None) -> bool:
    """Idempotent. Exports to OTLP when the endpoint env is set; a test may inject
    an in-memory exporter. Returns whether real tracing is active."""
    global _configured
    if _configured:
        return True
    if span_exporter is None and not config.otel_endpoint():
        return False  # stay on the global no-op tracer
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    if span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    otel.set_tracer_provider(provider)
    _configured = True
    return True


def _tracer():
    return otel.get_tracer(SERVICE_NAME)


def _token_delta(before: dict[str, int], after: dict[str, int] | None) -> dict[str, int]:
    if not after:
        return {}
    return {k: after.get(k, 0) - before.get(k, 0)
            for k in after if after.get(k, 0) != before.get(k, 0)}


def _result_update(result: Any) -> dict:
    """Node results are either partial dicts or Command(update=...)."""
    if isinstance(result, dict):
        return result
    update = getattr(result, "update", None)
    return update if isinstance(update, dict) else {}


GATE_NODES = {"plan_gate": "plan", "escalation_gate": "escalation", "merge_gate": "merge"}


def traced_node(name: str, fn: Callable) -> Callable:
    """Wrap a graph node in a span. GraphInterrupt (a gate parking the run) is not
    an error: the span records it and the park time feeds human_wait_ms on resume."""

    def wrapper(state):
        with _tracer().start_as_current_span(f"node.{name}") as span:
            t0 = time.monotonic()
            span.set_attribute("harness.node", name)
            span.set_attribute("harness.thread_id", state["thread_id"])
            span.set_attribute("harness.tenant_id", state["tenant_id"])
            span.set_attribute("harness.iteration", state["iteration_count"])
            span.set_attribute("harness.step", state["current_step"])
            gate = GATE_NODES.get(name)
            if gate:
                span.set_attribute("harness.gate", gate)
            try:
                result = fn(state)
            except GraphInterrupt:
                _PARKED_AT.setdefault((state["thread_id"], name), time.monotonic())
                span.set_attribute("harness.parked", True)
                span.set_attribute("duration_ms", (time.monotonic() - t0) * 1000)
                raise
            update = _result_update(result)
            span.set_attribute("duration_ms", (time.monotonic() - t0) * 1000)
            for node_key, delta in _token_delta(
                    state["token_usage"], update.get("token_usage")).items():
                span.set_attribute(f"harness.tokens.{node_key}", delta)
            if "status" in update:
                span.set_attribute("harness.status_after", update["status"])
            if gate:
                parked = _PARKED_AT.pop((state["thread_id"], name), None)
                span.set_attribute("harness.human_wait_ms",
                                   0.0 if parked is None else (time.monotonic() - parked) * 1000)
                decision = update.get("human_decision") or {}
                if decision.get("action"):
                    span.set_attribute("harness.decision", decision["action"])
                    span.set_attribute("harness.actor", decision.get("actor", "unknown"))
            verification = update.get("last_verification")
            if verification is not None:
                span.set_attribute("harness.verification.passed", verification["passed"])
                span.set_attribute("harness.verification.failed_tests",
                                   len(verification["failed_tests"]))
            return result

    return wrapper


@contextmanager
def run_span(kind: str, thread_id: str):
    """Root span around one graph invocation (start or resume)."""
    with _tracer().start_as_current_span(f"run.{kind}") as span:
        span.set_attribute("harness.thread_id", thread_id)
        yield span
