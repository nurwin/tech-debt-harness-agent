"""GitHub public-repo import — the only module that fetches external code.

Scope is deliberately narrow: `https://github.com/{owner}/{repo}` public repos.
The validator rejects every other scheme/host/userinfo form, and the clone runs
with terminal prompts and credential helpers disabled, so a private or
nonexistent repo fails fast with a surfaceable error instead of hanging on an
auth prompt — that is the "public only" enforcement.

An imported repo is UNTRUSTED code. The API therefore forces
workspace_kind="docker" for imported runs (see server.start): its tests/lint
only ever execute inside the locked-down sandbox, never on the orchestrator
host (CLAUDE.md rule 2 — the container is the security boundary).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

_CLONE_TIMEOUT_S = 120

# owner: GitHub user/org rules (alnum + inner hyphens); repo: alnum . _ -
GITHUB_URL_RE = re.compile(
    r"^https://github\.com/"
    r"([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"([A-Za-z0-9._-]+?)(?:\.git)?/?$"
)


class RepoImportError(Exception):
    """Import failure whose message is safe to return to the API caller."""


def validate_github_url(url: str) -> str:
    """Return the canonical clone URL, or raise ValueError for anything that is
    not a plain public-GitHub repository URL."""
    m = GITHUB_URL_RE.match(url.strip())
    if not m:
        raise ValueError(
            "repo_url must be a public GitHub repository URL of the form "
            "https://github.com/{owner}/{repo}"
        )
    owner, repo = m.groups()
    if repo in {".", ".."}:
        raise ValueError("invalid repository name")
    return f"https://github.com/{owner}/{repo}.git"


def clone(url: str, dest: Path, timeout: int = _CLONE_TIMEOUT_S) -> Path:
    """Shallow-clone `url` into `dest` (must not already exist)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "git",
        "-c", "credential.helper=",  # never pick up stored credentials
        "clone", "--depth", "1", "--single-branch", "--no-tags", "--quiet",
        url, str(dest),
    ]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/true")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise RepoImportError(f"clone timed out after {timeout}s") from None
    except FileNotFoundError:
        raise RepoImportError("git is not available on the orchestrator") from None
    if proc.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        tail = (proc.stderr or "").strip()[-400:]
        raise RepoImportError(f"clone failed (is the repository public?): {tail}")
    return dest


def import_github_repo(url: str, dest: Path) -> Path:
    """Validate then shallow-clone a public GitHub repo. Raises RepoImportError
    (clone) or ValueError (URL shape)."""
    return clone(validate_github_url(url), dest)
