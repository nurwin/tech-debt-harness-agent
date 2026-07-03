from pathlib import Path

import pytest

from src.sandbox.local import LocalWorkspace, prepare_workspace

FIXTURE_REPO = Path(__file__).parents[2] / "test-fixtures" / "sample-repo"


@pytest.fixture
def workspace(tmp_path) -> LocalWorkspace:
    """A disposable copy of the debt-demo fixture with a git baseline."""
    dest = prepare_workspace(FIXTURE_REPO, tmp_path, "tenant-a", "run-1")
    return LocalWorkspace("tenant-a", "run-1", dest)
