"""LocalWorkspace — Docker-free workspace for the dryrun path and CI.

Operates on a disposable host-side COPY of the tenant repo (prepare_workspace makes
the copy). It refuses to run against anything under test-fixtures/, which is the
pristine read-only source of truth (CLAUDE.md rule 8).
"""
import shutil
import subprocess
import sys
from pathlib import Path

from .base import BASELINE_REF, CmdResult, Workspace

_CMD_TIMEOUT_S = 180


def prepare_workspace(source_repo: str | Path, dest_root: str | Path,
                      tenant_id: str, thread_id: str) -> Path:
    """Copy the tenant repo into a per-(tenant, run) scratch dir; reuse on resume."""
    dest = Path(dest_root).resolve() / tenant_id / thread_id
    if not dest.exists():
        shutil.copytree(
            source_repo, dest,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".ruff_cache"),
        )
    return dest


class LocalWorkspace(Workspace):
    def __init__(self, tenant_id: str, thread_id: str, host_repo_path: str | Path):
        self.tenant_id = tenant_id
        self.thread_id = thread_id
        self.root = Path(host_repo_path).resolve()
        if "test-fixtures" in self.root.parts:
            raise ValueError(
                f"refusing to operate on pristine fixture source: {self.root} — "
                "copy it with prepare_workspace() first"
            )
        if not self.root.is_dir():
            raise FileNotFoundError(f"workspace dir does not exist: {self.root}")
        self.repo_path = str(self.root)
        self._ensure_git_baseline()

    # -- internals ---------------------------------------------------------

    def _git(self, *args: str) -> CmdResult:
        return self._run("git", "-c", "user.email=harness@local", "-c", "user.name=harness",
                         *args)

    def _run(self, *argv: str) -> CmdResult:
        proc = subprocess.run(
            argv, cwd=self.root, capture_output=True, text=True, timeout=_CMD_TIMEOUT_S,
        )
        return CmdResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def _ensure_git_baseline(self) -> None:
        """Create the rollback/diff baseline once; a resumed run finds it in place."""
        if not (self.root / ".git").is_dir():
            self._git("init", "-q")
        if not self._git("rev-parse", "--verify", "-q", BASELINE_REF).ok:
            self._git("add", "-A")
            self._git("commit", "-qm", BASELINE_REF, "--allow-empty")
            self._git("tag", BASELINE_REF)

    def _resolve_inside(self, relpath: str) -> Path:
        p = (self.root / relpath).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"path escapes workspace: {relpath}")
        return p

    # -- Workspace interface -------------------------------------------------

    def read_file(self, relpath: str) -> str:
        return self._resolve_inside(relpath).read_text()

    def write_file(self, relpath: str, content: str) -> None:
        p = self._resolve_inside(relpath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def run_tests(self) -> CmdResult:
        # `python -m pytest` puts cwd on sys.path, which the fixture's
        # `from app.api import app` imports rely on.
        return self._run(sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider")

    def run_lint(self) -> CmdResult:
        return self._run(sys.executable, "-m", "ruff", "check", ".", "--no-cache",
                         "--output-format", "concise")

    def list_files(self, suffix: str = ".py") -> list[str]:
        skip = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv"}
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob(f"*{suffix}")
            if not (skip & set(p.relative_to(self.root).parts))
        )

    def diff(self) -> str:
        return self._git("diff", BASELINE_REF).stdout

    def commit(self, message: str) -> None:
        self._git("add", "-A")
        self._git("commit", "-qm", message, "--allow-empty")

    def discard_uncommitted(self) -> None:
        self._git("reset", "--hard", "-q", "HEAD")
        self._git("clean", "-fdq")

    def rollback(self) -> None:
        self._git("reset", "--hard", "-q", BASELINE_REF)
        self._git("clean", "-fdq")

    def teardown(self) -> None:
        # Nothing to stop for a local dir; the per-run copy is left for inspection.
        pass
