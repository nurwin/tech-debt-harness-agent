"""Phase A integration tests. Skip cleanly when Docker or the executor image is
absent (never faked, never deleted — CLAUDE.md §4)."""
import pytest

from src import config
from src.sandbox.local import prepare_workspace
from src.sandbox.sandbox import Sandbox, docker_available, image_available
from tests.conftest import FIXTURE_REPO

pytestmark = [
    pytest.mark.skipif(not docker_available(), reason="Docker daemon not available"),
    pytest.mark.skipif(
        docker_available() and not image_available(config.sandbox_image()),
        reason=f"executor image {config.sandbox_image()!r} not built "
               "(run: docker build -t refactor-harness-executor executor-image/)",
    ),
]


@pytest.fixture
def sandbox(tmp_path):
    host = prepare_workspace(FIXTURE_REPO, tmp_path, "tenant-a", "sbx-test")
    sbx = Sandbox("tenant-a", "sbx-test", host)
    try:
        sbx.start()
        yield sbx
    finally:
        sbx.teardown()  # always torn down (checklist item)


def test_sandbox_lifecycle_write_read_test_teardown(sandbox):
    sandbox.write_file("hello.txt", "from the harness\n")
    assert sandbox.read_file("hello.txt") == "from the harness\n"

    tests = sandbox.run_tests()
    assert tests.exit_code != 0 and "6 failed" in tests.stdout  # pristine debt

    lint = sandbox.run_lint()
    assert "F401" in lint.stdout

    assert "app/db.py" in sandbox.list_files()
    sandbox.rollback()
    with pytest.raises(FileNotFoundError):
        sandbox.read_file("hello.txt")


def test_sandbox_is_locked_down(sandbox):
    # --network none: no interfaces beyond loopback, DNS resolution impossible
    net = sandbox._exec("python3", "-c",
                        "import socket; socket.gethostbyname('example.com')")
    assert net.exit_code != 0
    # --read-only root
    ro = sandbox._exec("sh", "-c", "touch /usr/marker 2>&1")
    assert ro.exit_code != 0 and "Read-only" in (ro.stdout + ro.stderr)
    # non-root user
    who = sandbox._exec("id", "-u")
    assert who.stdout.strip() == "1000"


def test_two_tenants_cannot_see_each_other(tmp_path):
    host_a = prepare_workspace(FIXTURE_REPO, tmp_path, "tenant-a", "iso")
    host_b = prepare_workspace(FIXTURE_REPO, tmp_path, "tenant-b", "iso")
    sbx_a = Sandbox("tenant-a", "iso", host_a)
    sbx_b = Sandbox("tenant-b", "iso", host_b)
    try:
        sbx_a.start()
        sbx_b.start()
        sbx_a.write_file("secret-a.txt", "tenant-a private data\n")
        # B's mount namespace has no path to A's workspace at all
        assert not sbx_b._exec("test", "-e", "/workspace/tenant-a").ok
        peek = sbx_b._exec("sh", "-c", "cat /workspace/*/secret-a.txt 2>&1")
        assert peek.exit_code != 0
        # and B has no network route to anything, including A
        assert not sbx_b._exec("getent", "hosts", sbx_a.name).ok
    finally:
        sbx_a.teardown()
        sbx_b.teardown()


def test_pi_binary_present_in_image(sandbox):
    res = sandbox._exec("pi", "--version")
    assert res.ok, res.stderr
    assert res.stdout.strip()
