"""Phase A — the Docker sandbox. THE security boundary (Pi has no permission system).

One ephemeral, locked-down container per (tenant, run):
  --network none (or a dedicated per-run bridge when the executor needs LLM egress),
  --read-only root + --tmpfs /tmp, non-root user, --cap-drop ALL,
  --security-opt no-new-privileges, memory/cpu limits, and the tenant repo mounted
  at a tenant-namespaced path. Nothing else in the codebase shells into Docker.

Isolation story: each run's container has a DIFFERENT host dir mounted at
/workspace/{tenant_id}; with no shared mounts, no shared network, and separate
namespaces, Agent A has no path, route, or handle to Agent B's filesystem.

Network trade-off (documented deviation): plan.md pairs `--network none` with Pi
calling the Anthropic API from inside the container — mutually exclusive. So:
verification sandboxes run with --network none; Pi sandboxes get a DEDICATED
per-run bridge network (no cross-container traffic, egress allowed). Prod uses a
K8s NetworkPolicy allowing egress only to the API endpoint (see README).
"""
import re
import shlex
import subprocess
from pathlib import Path

from .. import config
from .base import BASELINE_REF, CmdResult, Workspace

_EXEC_TIMEOUT_S = 300
_NAME_OK = re.compile(r"[^a-zA-Z0-9_.-]")


def _safe(name: str) -> str:
    return _NAME_OK.sub("-", name)


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def image_available(image: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", image],
                          capture_output=True, timeout=20).returncode == 0


class Sandbox(Workspace):
    def __init__(self, tenant_id: str, thread_id: str, host_repo_path: str | Path,
                 image: str | None = None, allow_egress: bool = False,
                 anthropic_api_key: str | None = None):
        self.tenant_id = tenant_id
        self.thread_id = thread_id
        self.host_repo_path = Path(host_repo_path).resolve()
        if "test-fixtures" in self.host_repo_path.parts:
            raise ValueError("refusing to mount pristine fixture source rw — copy it first")
        self.image = image or config.sandbox_image()
        self.allow_egress = allow_egress
        self._api_key = anthropic_api_key
        self.name = f"sbx-{_safe(tenant_id)}-{_safe(thread_id)}"
        self.net_name = f"{self.name}-net"
        self.repo_path = f"/workspace/{tenant_id}"
        self._started = False
        self._pi_proc: subprocess.Popen | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self.teardown_container()  # clear any stale container with our name
        net_args = ["--network", "none"]
        if self.allow_egress:
            subprocess.run(["docker", "network", "create", self.net_name],
                           capture_output=True, timeout=30)
            net_args = ["--network", self.net_name]
        env_args = ["-e", "HOME=/tmp"]  # non-root user has no homedir; git needs one
        if self._api_key and self.allow_egress:
            env_args += ["-e", f"ANTHROPIC_API_KEY={self._api_key}"]
        cmd = [
            "docker", "run", "-d",
            "--name", self.name,
            *net_args,
            "--read-only", "--tmpfs", "/tmp",
            "--user", "1000:1000",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", "512m", "--cpus", "1.0",
            *env_args,
            "-v", f"{config.translate_to_host_path(str(self.host_repo_path))}:{self.repo_path}:rw",
            "--workdir", self.repo_path,
            self.image, "sleep", "infinity",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"sandbox start failed: {proc.stderr.strip()}")
        self._started = True
        self._ensure_git_baseline()

    def teardown(self) -> None:
        if self._pi_proc is not None and self._pi_proc.poll() is None:
            self._pi_proc.kill()
        self._pi_proc = None
        self.teardown_container()
        subprocess.run(["docker", "network", "rm", self.net_name],
                       capture_output=True, timeout=30)
        self._started = False

    def teardown_container(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, timeout=60)

    # -- exec plumbing ---------------------------------------------------------

    def _exec(self, *argv: str, stdin: str | None = None) -> CmdResult:
        cmd = ["docker", "exec", "-i", self.name, *argv]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              input=stdin, timeout=_EXEC_TIMEOUT_S)
        return CmdResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def _git(self, *args: str) -> CmdResult:
        return self._exec("git", "-c", "user.email=harness@sandbox",
                          "-c", "user.name=harness", *args)

    def _ensure_git_baseline(self) -> None:
        self._git("config", "--global", "--add", "safe.directory", self.repo_path)
        if not self._exec("test", "-d", ".git").ok:
            self._git("init", "-q")
        if not self._git("rev-parse", "--verify", "-q", BASELINE_REF).ok:
            self._git("add", "-A")
            self._git("commit", "-qm", BASELINE_REF, "--allow-empty")
            self._git("tag", BASELINE_REF)

    def _guard_path(self, relpath: str) -> str:
        if relpath.startswith(("/", "~")) or ".." in Path(relpath).parts:
            raise ValueError(f"path escapes workspace: {relpath}")
        return relpath

    # -- Workspace interface ---------------------------------------------------

    def read_file(self, relpath: str) -> str:
        res = self._exec("cat", self._guard_path(relpath))
        if not res.ok:
            raise FileNotFoundError(res.stderr.strip())
        return res.stdout

    def write_file(self, relpath: str, content: str) -> None:
        rel = self._guard_path(relpath)
        parent = str(Path(rel).parent)
        res = self._exec(
            "sh", "-c",
            f"mkdir -p {shlex.quote(parent)} && cat > {shlex.quote(rel)}",
            stdin=content,
        )
        if not res.ok:
            raise IOError(f"write failed: {res.stderr.strip()}")

    def list_files(self, suffix: str = ".py") -> list[str]:
        res = self._exec("sh", "-c",
                         f"find . -name '*{suffix}' -not -path './.git/*' "
                         "-not -path '*/__pycache__/*' | sed 's|^\\./||' | sort")
        return [line for line in res.stdout.splitlines() if line]

    def run_tests(self) -> CmdResult:
        return self._exec("python3", "-m", "pytest", "-q", "--no-header",
                          "-p", "no:cacheprovider")

    def run_lint(self) -> CmdResult:
        return self._exec("python3", "-m", "ruff", "check", ".", "--no-cache",
                          "--output-format", "concise")

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

    # -- Pi (Phase C) ----------------------------------------------------------

    def start_pi_rpc(self, tools: str = "read,write,edit,bash") -> subprocess.Popen:
        """Long-lived `pi --mode rpc` INSIDE the container (never on the host —
        CLAUDE.md rule 2). Binary pipes: the RPC framing layer splits on \\n only."""
        if self._pi_proc is None or self._pi_proc.poll() is not None:
            self._pi_proc = subprocess.Popen(
                ["docker", "exec", "-i", "--workdir", self.repo_path, self.name,
                 "pi", "--mode", "rpc", "--no-session", "--tools", tools],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        return self._pi_proc
