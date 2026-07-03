"""Phase C — PiAdapter: drives the Pi coding agent headless over RPC in the sandbox.

Framing (CLAUDE.md hard rule 4): Pi's RPC is strict LF-delimited JSONL. We read the
BINARY stream and split on b"\\n" ONLY — binary readline never splits on U+2028/U+2029
(Python only treats those as line breaks in str.splitlines / text mode), so JSON
string payloads containing them survive intact.

Event/usage shapes are taken from earendil-works/pi docs (packages/coding-agent/docs/
rpc.md): we wait for {"type": "agent_end"} and permissively harvest token usage from
any event carrying a usage object. If RPC usage proves unavailable, the documented
fallback is one-shot `pi -p --mode json`, which reports usage per invocation
(plan.md Phase C) — see PiAdapter.oneshot_fallback.
"""
import json
import subprocess
from typing import Any

from ..agents.prompts import EXECUTOR_SYSTEM, executor_step_prompt
from ..sandbox.base import Workspace
from ..sandbox.sandbox import Sandbox
from ..state import ErrorRecord, PlanStep
from .base import ExecutorAdapter, ExecutorResult

_STEP_TIMEOUT_S = 600


class PiRpcError(RuntimeError):
    pass


class PiRpcClient:
    """Minimal LF-delimited JSONL client over a Popen's binary stdio."""

    def __init__(self, proc: subprocess.Popen):
        if proc.stdin is None or proc.stdout is None:
            raise ValueError("pi process must have piped stdin/stdout")
        self.proc = proc

    def send(self, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def read_event(self) -> dict[str, Any]:
        """One event per LF-terminated line. Binary readline splits on \\n ONLY."""
        while True:
            raw = self.proc.stdout.readline()
            if raw == b"":
                stderr = b""
                if self.proc.stderr is not None:
                    stderr = self.proc.stderr.read() or b""
                raise PiRpcError(
                    f"pi RPC stream closed (exit={self.proc.poll()}): "
                    f"{stderr.decode('utf-8', 'replace')[-2000:]}"
                )
            line = raw.rstrip(b"\n")
            if not line.strip():
                continue
            try:
                return json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue  # non-JSON noise on stdout: ignore, keep framing


def harvest_usage(event: dict[str, Any]) -> int:
    """Permissively sum input+output tokens from any usage-bearing event."""
    found = 0

    def walk(node: Any) -> None:
        nonlocal found
        if isinstance(node, dict):
            usage = node.get("usage")
            if isinstance(usage, dict):
                for key in ("input_tokens", "output_tokens", "input", "output",
                            "cacheRead", "cache_read_input_tokens"):
                    val = usage.get(key)
                    if isinstance(val, (int, float)):
                        found += int(val)
                return  # don't double-count nested duplicates of the same usage
            for v in node.values():
                walk(v)

    walk(event)
    return found


class PiAdapter(ExecutorAdapter):
    name = "pi"

    def apply_step(self, workspace: Workspace, step: PlanStep,
                   prior_error: ErrorRecord | None,
                   guidance: str | None = None) -> ExecutorResult:
        if not isinstance(workspace, Sandbox):
            # Hard rule 2: Pi only ever runs inside the locked-down container.
            raise PiRpcError("PiAdapter requires the Docker Sandbox workspace")

        prior_text = None
        if prior_error is not None:
            prior_text = (
                f"iteration {prior_error.iteration} failed.\n"
                f"failed tests: {prior_error.failed_tests}\n"
                f"lint errors: {prior_error.lint_errors}\n"
                f"pytest output tail:\n{prior_error.stdout[-1500:]}"
            )
        file_content = workspace.read_file(step.file)
        # No verified flag for a per-request system prompt in RPC mode, so the
        # system contract is prepended to the message (don't invent flags — CLAUDE.md §10).
        prompt = EXECUTOR_SYSTEM + "\n\n" + executor_step_prompt(
            step.file, step.rationale, file_content, prior_text, guidance)

        client = PiRpcClient(workspace.start_pi_rpc())
        client.send({"type": "prompt", "message": prompt})

        tokens = 0
        events: list[dict[str, Any]] = []
        while True:
            event = client.read_event()
            events.append(event)
            tokens += harvest_usage(event)
            if event.get("type") == "agent_end":
                break
            if event.get("type") == "error":
                raise PiRpcError(f"pi reported an error: {json.dumps(event)[:2000]}")

        return ExecutorResult(
            action="edited", file=step.file, tokens=tokens,
            raw={"adapter": self.name, "events": len(events)},
        )

    @staticmethod
    def oneshot_fallback(sandbox: Sandbox, prompt: str) -> dict[str, Any]:
        """Documented fallback: `pi -p --mode json "<prompt>"` one-shot, which
        reports cost/usage per invocation. Kept small and unused by default."""
        res = sandbox._exec("pi", "-p", "--mode", "json", prompt)
        if not res.ok:
            raise PiRpcError(f"pi one-shot failed: {res.stderr[-2000:]}")
        return json.loads(res.stdout)
