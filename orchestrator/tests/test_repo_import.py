"""GitHub public-repo import.

Offline (always run): URL validation matrix, clone semantics against a local
git origin (file:// — no network), API refusal paths, and a full imported run
end-to-end with the Sandbox stubbed to a local workspace.
Docker-gated: the same imported run inside the real sandbox (clone stubbed from
the fixture, so still network-free). A true-network clone test is opt-in via
RUN_NETWORK_TESTS.
"""
import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import src.sandbox.sandbox as sandbox_mod
from src import config, repo_import
from src.api.server import create_app
from src.executor_adapters import reset_adapters
from src.repo_import import RepoImportError, clone, validate_github_url
from src.sandbox.local import LocalWorkspace
from src.sandbox.registry import drop_workspace, reset_workspaces
from tests.conftest import FIXTURE_REPO
from tests.test_api_dryrun import pending_gate, wait_status

# ------------------------------------------------------------ URL validation


@pytest.mark.parametrize("url, canonical", [
    ("https://github.com/owner/repo", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo.git", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo/", "https://github.com/owner/repo.git"),
    ("  https://github.com/o-w-n3r/some.repo_x ",
     "https://github.com/o-w-n3r/some.repo_x.git"),
])
def test_accepts_public_github_urls(url, canonical):
    assert validate_github_url(url) == canonical


@pytest.mark.parametrize("url", [
    "http://github.com/owner/repo",               # not https
    "https://gitlab.com/owner/repo",              # wrong host
    "https://github.com.evil.io/owner/repo",      # host-suffix trick
    "https://user:token@github.com/owner/repo",   # credentials smuggled in
    "git@github.com:owner/repo.git",              # ssh form
    "ssh://git@github.com/owner/repo",            # ssh scheme
    "https://github.com/owner",                   # no repo
    "https://github.com/owner/repo/tree/main",    # extra path segments
    "https://github.com/-owner/repo",             # invalid owner
    "https://github.com/owner/..",                # traversal as repo name
    "file:///etc",                                # local filesystem
    "",
])
def test_rejects_everything_else(url):
    with pytest.raises(ValueError):
        validate_github_url(url)


# ------------------------------------------------------------ clone (offline)


@pytest.fixture
def local_origin(tmp_path):
    """A local git repo standing in for GitHub; clone() is exercised via file://."""
    src = tmp_path / "origin"
    src.mkdir()
    (src / "marker.py").write_text("VALUE = 1\n")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], cwd=src, check=True)
    subprocess.run([*git, "add", "-A"], cwd=src, check=True)
    subprocess.run([*git, "commit", "-qm", "seed"], cwd=src, check=True)
    return src


def test_clone_from_local_origin(tmp_path, local_origin):
    dest = clone(f"file://{local_origin}", tmp_path / "clone")
    assert (dest / "marker.py").read_text() == "VALUE = 1\n"


def test_clone_failure_is_structured_and_cleans_up(tmp_path):
    dest = tmp_path / "clone"
    with pytest.raises(RepoImportError, match="public"):
        clone("file:///definitely/not/a/repo", dest)
    assert not dest.exists()


# ------------------------------------------------------------ API paths


@pytest.fixture
def client(tmp_path):
    reset_workspaces()
    reset_adapters()
    app = create_app(checkpoint_db=str(tmp_path / "api-ckpt.sqlite"),
                     runs_root=str(tmp_path / "runs"),
                     target_repo=str(FIXTURE_REPO))
    with TestClient(app) as c:
        yield c


def test_api_rejects_bad_repo_url(client):
    r = client.post("/runs", json={"tenant_id": "t",
                                   "repo_url": "https://gitlab.com/o/r"})
    assert r.status_code == 422
    assert "github.com" in r.text


def test_api_refuses_import_without_docker(client, monkeypatch):
    # Untrusted code never runs on the orchestrator host: no sandbox, no import.
    monkeypatch.setattr(sandbox_mod, "docker_available", lambda: False)
    r = client.post("/runs", json={"tenant_id": "t",
                                   "repo_url": "https://github.com/o/r"})
    assert r.status_code == 400
    assert "Docker" in r.json()["detail"]


class _StubSandbox(LocalWorkspace):
    """Stands in for Sandbox so the imported-run path is testable offline.
    Signature and start() match what the workspace registry calls."""

    def start(self) -> "_StubSandbox":
        return self


@pytest.fixture
def stubbed_docker(monkeypatch, tmp_path):
    """Pretend Docker exists and clone by copying the fixture — no network."""
    monkeypatch.setattr(sandbox_mod, "docker_available", lambda: True)
    monkeypatch.setattr(sandbox_mod, "image_available", lambda image: True)
    monkeypatch.setattr(sandbox_mod, "Sandbox", _StubSandbox)
    seen: dict[str, str] = {}

    def fake_import(url, dest):
        seen["url"] = url
        shutil.copytree(FIXTURE_REPO, dest)
        return dest

    monkeypatch.setattr(repo_import, "import_github_repo", fake_import)
    return seen


def test_imported_run_end_to_end_offline(client, tmp_path, stubbed_docker):
    r = client.post("/runs", json={"tenant_id": "tenant-i",
                                   "repo_url": "https://github.com/octo/demo"})
    assert r.status_code == 201
    tid = r.json()["thread_id"]

    # canonicalized by the schema before the cloner ever sees it
    assert stubbed_docker["url"] == "https://github.com/octo/demo.git"
    # workspace was copied FROM the clone, and the clone dir was removed
    runs_dir = tmp_path / "runs"
    assert (runs_dir / "tenant-i" / tid / "app" / "db.py").exists()
    assert not (runs_dir / ".imports" / tid).exists()

    plan = pending_gate(client, tid, "plan")
    assert [s["file"] for s in plan["plan"]] == ["app/db.py", "app/api.py"]

    state = client.get(f"/runs/{tid}/state").json()
    assert state["source_repo_url"] == "https://github.com/octo/demo.git"

    client.post(f"/runs/{tid}/decision", json={"action": "approve"})
    pending_gate(client, tid, "merge")
    client.post(f"/runs/{tid}/decision", json={"action": "approve"})
    final = wait_status(client, tid, "succeeded")
    assert final["completed_steps"] == [0, 1]


# ------------------------------------------------------------ Docker-gated


@pytest.mark.skipif(not sandbox_mod.docker_available(),
                    reason="Docker daemon not available")
@pytest.mark.skipif(
    sandbox_mod.docker_available()
    and not sandbox_mod.image_available(config.sandbox_image()),
    reason=f"executor image {config.sandbox_image()!r} not built",
)
def test_imported_run_in_real_sandbox(client, monkeypatch):
    """Imported repos always verify inside the real locked-down sandbox.
    The clone is stubbed from the fixture so the test stays network-free."""
    def fake_import(url, dest):
        shutil.copytree(FIXTURE_REPO, dest)
        return dest

    monkeypatch.setattr(repo_import, "import_github_repo", fake_import)
    tid = client.post("/runs", json={
        "tenant_id": "tenant-dkr", "auto_approve": True,
        "repo_url": "https://github.com/octo/demo",
    }).json()["thread_id"]
    try:
        final = wait_status(client, tid, "succeeded", "aborted", "failed")
        assert final["status"] == "succeeded"
        assert final["source_repo_url"] == "https://github.com/octo/demo.git"
    finally:
        drop_workspace(tid)  # tears the container down


# ------------------------------------------------------------ opt-in network


@pytest.mark.skipif(not os.environ.get("RUN_NETWORK_TESTS"),
                    reason="set RUN_NETWORK_TESTS=1 to clone from real GitHub")
def test_real_github_clone(tmp_path):
    dest = repo_import.import_github_repo("https://github.com/octocat/Hello-World",
                                          tmp_path / "hw")
    assert (dest / "README").exists()
