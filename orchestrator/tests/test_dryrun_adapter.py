"""Phase B: DryRun adapter applies the known-correct fix; Flaky/AlwaysFail inject failures."""
import pytest

from src.executor_adapters.dryrun_adapter import (
    AlwaysFailAdapter,
    DryRunAdapter,
    FlakyAdapter,
)
from src.state import PlanStep

STEP_DB = PlanStep(step_id=0, file="app/db.py", change_type="refactor",
                   rationale="convert data layer to async")
STEP_API = PlanStep(step_id=1, file="app/api.py", change_type="refactor",
                    rationale="validation + status codes + await db")


def test_fixture_baseline_is_red(workspace):
    """Pristine fixture: 6 failing tests and at least one lint error (unused import)."""
    tests = workspace.run_tests()
    assert tests.exit_code != 0
    assert "6 failed" in tests.stdout
    lint = workspace.run_lint()
    assert lint.exit_code != 0
    assert "F401" in lint.stdout


def test_dryrun_fix_turns_suite_green(workspace):
    adapter = DryRunAdapter()
    adapter.apply_step(workspace, STEP_DB, None)
    adapter.apply_step(workspace, STEP_API, None)
    assert "async def get_user" in workspace.read_file("app/db.py")
    tests = workspace.run_tests()
    assert tests.exit_code == 0, tests.stdout
    assert "9 passed" in tests.stdout
    lint = workspace.run_lint()
    assert lint.exit_code == 0, lint.stdout
    assert adapter.calls == [0, 1]


def test_dryrun_reports_deterministic_nonzero_tokens(workspace):
    adapter = DryRunAdapter()
    r1 = adapter.apply_step(workspace, STEP_DB, None)
    r2 = adapter.apply_step(workspace, STEP_API, None)
    assert (r1.tokens, r2.tokens) == (340, 560)
    # retry charges the same deterministic amount again
    r3 = adapter.apply_step(workspace, STEP_API, None)
    assert r3.tokens == 560


def test_flaky_adapter_fails_then_succeeds(workspace):
    adapter = FlakyAdapter(fail_times=2)
    adapter.apply_step(workspace, STEP_DB, None)  # attempt 1: broken (new lint error)
    lint = workspace.run_lint()
    assert "os" in lint.stdout and "F401" in lint.stdout
    adapter.apply_step(workspace, STEP_DB, None)  # attempt 2: still broken
    adapter.apply_step(workspace, STEP_DB, None)  # attempt 3: correct
    assert workspace.read_file("app/db.py").startswith('"""Data-access layer (async).')


def test_alwaysfail_adapter_never_converges(workspace):
    adapter = AlwaysFailAdapter()
    adapter.apply_step(workspace, STEP_DB, None)
    adapter.apply_step(workspace, STEP_API, None)
    tests = workspace.run_tests()
    assert tests.exit_code != 0  # 201 test still failing


def test_rollback_restores_baseline(workspace):
    DryRunAdapter().apply_step(workspace, STEP_DB, None)
    assert "async def get_user" in workspace.read_file("app/db.py")
    assert workspace.diff().strip() != ""
    workspace.rollback()
    assert "time.sleep" in workspace.read_file("app/db.py")  # pristine sync version
    assert "async def get_user" not in workspace.read_file("app/db.py")
    assert workspace.diff().strip() == ""


def test_workspace_refuses_fixture_source():
    from src.sandbox.local import LocalWorkspace
    from tests.conftest import FIXTURE_REPO
    with pytest.raises(ValueError, match="pristine fixture"):
        LocalWorkspace("t", "r", FIXTURE_REPO)
