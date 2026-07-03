"""Central configuration. All env access lives here — nodes never read os.environ."""
import os

# Hard cost guardrail (CLAUDE.md rule 5). The verification loop hard-aborts and
# rolls back at this count. Never weaken, never make configurable via env.
MAX_ITERATIONS = 3

# The escalation gate may grant at most one human-approved retry (which resets
# iteration_count once). After that, abort is unconditional.
MAX_ESCALATIONS = 1


def executor_adapter_name() -> str:
    """Which ExecutorAdapter to use: 'dryrun' (default, no LLM/Pi) or 'pi'."""
    return os.environ.get("EXECUTOR_ADAPTER", "dryrun")


def checkpoint_db_path() -> str:
    return os.environ.get("CHECKPOINT_DB", "checkpoints/harness.sqlite")


def anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None


def otel_endpoint() -> str | None:
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None


def sandbox_image() -> str:
    return os.environ.get("SANDBOX_IMAGE", "refactor-harness-executor:latest")


def jaeger_ui_url() -> str:
    return os.environ.get("JAEGER_UI_URL", "http://localhost:16686").rstrip("/")


def runs_root() -> str:
    """Host directory under which per-run workspace copies are created."""
    return os.environ.get("RUNS_ROOT", ".runs")
