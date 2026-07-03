"""Workspace abstraction shared by the Docker sandbox and the local dryrun workspace.

Every node interacts with the tenant repo ONLY through this interface, so the whole
graph runs identically against:
  * LocalWorkspace  — a disposable host-side copy (dryrun tests / CI, no Docker), and
  * Sandbox         — the locked-down per-(tenant, run) Docker container (production).
"""
from abc import ABC, abstractmethod

from pydantic import BaseModel

BASELINE_REF = "__harness_baseline__"


class CmdResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class Workspace(ABC):
    tenant_id: str
    thread_id: str
    repo_path: str  # path the executor sees (inside the container for Sandbox)

    @abstractmethod
    def read_file(self, relpath: str) -> str: ...

    @abstractmethod
    def write_file(self, relpath: str, content: str) -> None: ...

    @abstractmethod
    def run_tests(self) -> CmdResult: ...

    @abstractmethod
    def run_lint(self) -> CmdResult: ...

    @abstractmethod
    def list_files(self, suffix: str = ".py") -> list[str]:
        """Repo-relative source files (skips .git, caches, virtualenvs)."""
        ...

    @abstractmethod
    def diff(self) -> str:
        """Unified diff of the workspace vs the baseline commit."""
        ...

    @abstractmethod
    def commit(self, message: str) -> None:
        """Commit current workspace state — called after each VERIFIED step, so
        accept_partial/rollback have step-granular ground truth."""
        ...

    @abstractmethod
    def discard_uncommitted(self) -> None:
        """Drop uncommitted changes (a failed attempt), keeping verified step commits."""
        ...

    @abstractmethod
    def rollback(self) -> None:
        """Hard-reset the workspace to the baseline commit."""
        ...

    @abstractmethod
    def teardown(self) -> None: ...
