"""ExecutorAdapter ABC — the seam that makes the executor swappable.

The graph only ever talks to this interface. PiAdapter drives the Pi coding agent
over RPC inside the Docker sandbox; DryRunAdapter applies known-correct fixes with
zero LLM cost so the entire graph is CI-testable offline. Swapping Pi for Claude
Code/Codex later means writing one new adapter, nothing else changes.
"""
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from ..sandbox.base import Workspace
from ..state import ErrorRecord, PlanStep


class ExecutorResult(BaseModel):
    action: str  # e.g. "edited", "noop"
    file: str
    tokens: int
    raw: dict[str, Any] | None = None


class ExecutorAdapter(ABC):
    name: str

    @abstractmethod
    def apply_step(
        self,
        workspace: Workspace,
        step: PlanStep,
        prior_error: ErrorRecord | None,
        guidance: str | None = None,
    ) -> ExecutorResult:
        """Apply one plan step to the workspace. `prior_error` carries the structured
        failure from the previous verification iteration on retries; `guidance` is
        optional human text granted with an escalation-gate retry."""
        ...
