"""Deterministic adapters for testing the whole graph offline (no LLM, no Pi, no Docker).

DryRunAdapter applies the known-correct fix for the vendored debt-demo fixture and
reports deterministic NON-ZERO token counts. (plan.md Phase B says tokens=0, but the
crash-resume test must prove token totals don't double on resume — unobservable at
zero — so we deliberately use fixed non-zero counts, per CLAUDE.md §6.)

FlakyAdapter fails N times then succeeds (exercises the self-correction loop).
AlwaysFailAdapter never succeeds (exercises the hard abort at MAX_ITERATIONS=3).
"""
from ..sandbox.base import Workspace
from ..state import ErrorRecord, PlanStep
from .base import ExecutorAdapter, ExecutorResult

# Known-correct final contents for the debt-demo fixture. db.py first (leaf),
# api.py second — the pair is the final state; the suite is only green once
# BOTH are applied (the sync->async cut is atomic across the two files).

FIX_DB = '''"""Data-access layer (async).

Refactored: async def + await asyncio.sleep, so calls from async handlers no
longer block the event loop. Observable return values are unchanged.
"""
import asyncio

_USERS: dict[int, dict] = {
    1: {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com"},
}
_NEXT_ID = 2


async def _simulate_io() -> None:
    await asyncio.sleep(0.01)


async def get_user(user_id: int) -> dict | None:
    """Fetch a single user by id, or None if absent."""
    await _simulate_io()
    return _USERS.get(user_id)


async def list_users() -> list[dict]:
    """Return all users."""
    await _simulate_io()
    return list(_USERS.values())


async def insert_user(name: str, email: str) -> dict:
    """Insert a new user and return the created record."""
    global _NEXT_ID
    await _simulate_io()
    record = {"id": _NEXT_ID, "name": name, "email": email}
    _USERS[_NEXT_ID] = record
    _NEXT_ID += 1
    return record


async def delete_user(user_id: int) -> bool:
    """Delete a user; return True if it existed."""
    await _simulate_io()
    return _USERS.pop(user_id, None) is not None


def _reset_for_tests() -> None:
    """Test helper: restore pristine seed state."""
    global _USERS, _NEXT_ID
    _USERS = {1: {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com"}}
    _NEXT_ID = 2
'''

FIX_API = '''"""HTTP API layer (FastAPI) — refactored.

* Pydantic UserCreate validates input: 422 on blank name / missing or bad email.
* Correct status codes: 201 on create, 404 on missing user.
* Awaits the now-async data layer, so the event loop is never blocked.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, field_validator

from . import db

app = FastAPI(title="Debt Demo API")


class UserCreate(BaseModel):
    name: str
    email: EmailStr

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/users")
async def get_users():
    return await db.list_users()


@app.get("/users/{user_id}")
async def read_user(user_id: int):
    user = await db.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@app.post("/users", status_code=201)
async def create_user(payload: UserCreate):
    return await db.insert_user(payload.name, payload.email)


@app.delete("/users/{user_id}")
async def remove_user(user_id: int):
    deleted = await db.delete_user(user_id)
    return {"deleted": deleted}
'''

KNOWN_FIXES: dict[str, str] = {
    "app/db.py": FIX_DB,
    "app/api.py": FIX_API,
}

# Deliberately-wrong variants used by the failure-injecting adapters. Each is a
# *plausible* near-miss: api.py misses status_code=201 (one test fails); db.py
# carries an unused import (a NEW lint error vs baseline).
BROKEN_FIXES: dict[str, str] = {
    "app/db.py": "import os\n" + FIX_DB,
    "app/api.py": FIX_API.replace('@app.post("/users", status_code=201)', '@app.post("/users")'),
}

# Deterministic per-file token counts, charged on every apply (including retries),
# so cost accounting is exactly reproducible in tests.
DRYRUN_TOKENS: dict[str, int] = {
    "app/db.py": 340,
    "app/api.py": 560,
}


class DryRunAdapter(ExecutorAdapter):
    name = "dryrun"

    def __init__(self) -> None:
        # (step_id, iteration-ish attempt no.) per call — crash-resume asserts on this.
        self.calls: list[int] = []

    def apply_step(self, workspace: Workspace, step: PlanStep,
                   prior_error: ErrorRecord | None,
                   guidance: str | None = None) -> ExecutorResult:
        self.calls.append(step.step_id)
        content = self._content_for(step)
        workspace.write_file(step.file, content)
        return ExecutorResult(
            action="edited", file=step.file,
            tokens=DRYRUN_TOKENS.get(step.file, 100),
            raw={"adapter": self.name, "prior_error": prior_error is not None},
        )

    def _content_for(self, step: PlanStep) -> str:
        if step.file not in KNOWN_FIXES:
            raise ValueError(f"DryRunAdapter has no known fix for {step.file}")
        return KNOWN_FIXES[step.file]


class FlakyAdapter(DryRunAdapter):
    """Writes a broken near-miss for the first `fail_times` attempts on each step
    (or only on `only_step` if given), then the correct fix — drives the
    verifier's self-correction loop."""

    name = "flaky"

    def __init__(self, fail_times: int = 2, only_step: int | None = None) -> None:
        super().__init__()
        self.fail_times = fail_times
        self.only_step = only_step
        self._attempts: dict[int, int] = {}

    def _content_for(self, step: PlanStep) -> str:
        if self.only_step is not None and step.step_id != self.only_step:
            return KNOWN_FIXES[step.file]
        attempt = self._attempts.get(step.step_id, 0)
        self._attempts[step.step_id] = attempt + 1
        if attempt < self.fail_times:
            return BROKEN_FIXES[step.file]
        return KNOWN_FIXES[step.file]


class AlwaysFailAdapter(DryRunAdapter):
    """Never converges — drives the hard abort + rollback at MAX_ITERATIONS=3."""

    name = "alwaysfail"

    def _content_for(self, step: PlanStep) -> str:
        return BROKEN_FIXES[step.file]
