# Debt Demo API

A deliberately flawed FastAPI service. **This is not a real product** — it exists to be the
*target repository* that the Refactoring Harness operates on. It ships with known
architectural debt and a test suite that fails until that debt is fixed.

Keep this repo **separate** from the harness. The harness clones/mounts it, plans a
multi-file refactor, executes edits in a sandbox, and runs these tests in its verification
loop.

## The intentional debt

| # | Debt | File | Fix the tests expect |
|---|------|------|----------------------|
| 1 | Synchronous blocking calls (`time.sleep`) in the data layer, called from async handlers — blocks the event loop | `app/db.py` | Convert to `async def` + `await asyncio.sleep` |
| 2 | No input validation on user creation (accepts blank name, missing/malformed email) | `app/api.py` | Add a Pydantic `UserCreate` model; return **422** on bad input |
| 3 | Wrong status codes: create returns 200 (not 201); missing user returns 200 + null (not 404) | `app/api.py` | Return **201** on create, **404** on missing user |

The refactor is deliberately **multi-file** and **dependency-ordered** (`db.py` is a leaf;
`api.py` depends on it), so a planner has a real ordering decision to make.

## Layout

```
debt-demo-api/
├── app/
│   ├── db.py        # data layer (debt: sync/blocking)
│   └── api.py       # FastAPI layer (debt: no validation, wrong codes)
├── tests/
│   └── test_api.py  # behavioral contract — FAILS until refactor is done
├── requirements.txt
└── pyproject.toml
```

## Run the tests

```bash
pip install -r requirements.txt
pytest -q
```

**Expected on the unmodified repo:** 6 failed, 3 passed. Those 6 failures are the work the
harness must complete. When all 9 pass, the refactor is correct.

## Contract notes

The tests assert **observable behavior only** (status codes, validation, 404s) — never
implementation details — so the harness is free to convert the data layer to async without
breaking anything. `db._reset_for_tests()` restores pristine seed state between tests.
