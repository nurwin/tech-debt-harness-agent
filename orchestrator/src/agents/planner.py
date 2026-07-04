"""Stage 1 — Planner (read-only).

Builds the dependency-ordered JSON plan and captures the run's verification
baseline (the fixture is deliberately red at the start; see state.py).

Two modes:
  * deterministic: an AST import scan orders candidate files leaf-first — used by
    every dryrun-family adapter so the whole graph runs offline (plan.md Phase D
    allows a stubbed planner).
  * LLM: Anthropic call with PLANNER_SYSTEM emitting strict JSON, validated with
    pydantic and re-ordered leaf-first as a safety net. Used when the executor is Pi.
"""
import ast
import json
from pathlib import PurePosixPath

from pydantic import BaseModel, ValidationError

from .. import config
from ..sandbox.base import Workspace
from ..sandbox.registry import get_workspace
from ..state import HarnessState, PlanStep, add_tokens
from .prompts import PLANNER_SYSTEM

# Deterministic token charge for the offline planner, so cost accounting is
# meaningful (and crash-resume can assert totals don't double).
DRYRUN_PLANNER_TOKENS = 200

_SKIP_PARTS = {"tests", "test", "conftest.py", "setup.py"}


class _PlanDoc(BaseModel):
    steps: list[PlanStep]


def order_leaf_first(files: dict[str, str]) -> list[str]:
    """Topological order by imports: a file comes before every file that imports it.

    (plan.md says "fewest dependents first", but the worked example — db.py before
    api.py, where api imports db — is dependency-leaf-first, i.e. topological.)
    """
    short_names = {path: PurePosixPath(path).stem for path in files}
    deps: dict[str, set[str]] = {path: set() for path in files}
    for path, source in files.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                    imported |= {f"{node.module}.{a.name}" for a in node.names}
                if node.level:  # relative import: `from . import db`
                    imported |= {a.name for a in node.names}
        for other, stem in short_names.items():
            if other != path and any(
                imp == stem or imp.endswith(f".{stem}") for imp in imported
            ):
                deps[path].add(other)
    ordered: list[str] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(p for p, d in remaining.items() if not (d & remaining.keys()))
        if not ready:  # cycle — fall back to name order, still deterministic
            ready = sorted(remaining)
        ordered += ready
        for p in ready:
            remaining.pop(p)
    return ordered


def _candidate_files(workspace: Workspace) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in workspace.list_files(".py"):
        if set(PurePosixPath(rel).parts) & _SKIP_PARTS:
            continue
        if PurePosixPath(rel).name == "__init__.py":
            continue
        out[rel] = workspace.read_file(rel)
    return out


def _deterministic_plan(workspace: Workspace) -> tuple[list[PlanStep], int]:
    files = _candidate_files(workspace)
    ordered = order_leaf_first(files)
    steps = [
        PlanStep(
            step_id=i,
            file=path,
            change_type="refactor",
            rationale=f"Remove the documented architectural debt in {path} so the "
                      "repo test contract passes (validation, status codes, async data layer).",
        )
        for i, path in enumerate(ordered)
    ]
    return steps, DRYRUN_PLANNER_TOKENS


def _llm_plan(workspace: Workspace) -> tuple[list[PlanStep], int]:
    """Anthropic-backed planner. Only reached when the executor adapter is Pi."""
    import anthropic

    files = _candidate_files(workspace)
    readme = ""
    try:
        readme = workspace.read_file("README.md")
    except FileNotFoundError:
        pass
    listing = "\n\n".join(
        f"=== {path} ===\n{source}" for path, source in sorted(files.items())
    )
    # base_url=None → the SDK's default (api.anthropic.com); a set value points
    # at any Anthropic-compatible endpoint (e.g. Xiaomi MiMo Token Plan).
    client = anthropic.Anthropic(api_key=config.anthropic_api_key(),
                                 base_url=config.anthropic_base_url())
    msg = client.messages.create(
        model=config.llm_model() or "claude-sonnet-5",
        max_tokens=2000,
        system=PLANNER_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Repository README:\n{readme}\n\nSource files:\n{listing}",
        }],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if text.startswith("```"):  # tolerate a fenced response despite the prompt
        text = text.strip("`").removeprefix("json").strip()
    try:
        doc = _PlanDoc.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"planner emitted an invalid plan: {exc}") from exc
    # Safety net: re-normalize ids and enforce leaf-first order regardless of the LLM.
    ordered = order_leaf_first({s.file: files.get(s.file, "") for s in doc.steps})
    by_file = {s.file: s for s in doc.steps}
    steps = [
        by_file[f].model_copy(update={"step_id": i, "status": "pending"})
        for i, f in enumerate(ordered)
    ]
    tokens = msg.usage.input_tokens + msg.usage.output_tokens
    return steps, tokens


def planner(state: HarnessState) -> dict:
    workspace = get_workspace(state)

    # Capture the verification baseline (see state.py for why intermediate steps
    # are gated relative to it).
    from .verifier import parse_failed_tests, parse_lint_errors

    baseline_tests = workspace.run_tests()
    baseline_lint = workspace.run_lint()

    if state["executor_adapter"] == "pi":
        steps, tokens = _llm_plan(workspace)
    else:
        steps, tokens = _deterministic_plan(workspace)
    if not steps:
        raise ValueError("planner produced an empty plan")

    return {
        "plan": steps,
        "current_step": 0,
        "baseline_failed_tests": parse_failed_tests(baseline_tests.stdout),
        "baseline_lint_errors": parse_lint_errors(baseline_lint.stdout),
        "token_usage": add_tokens(state["token_usage"], "planner", tokens),
        "status": "awaiting_human",
        "pending_approval": "plan",
    }
