"""
HTTP API layer (FastAPI).

ARCHITECTURAL DEBT (intentional — target of the refactor):
  1. NO INPUT VALIDATION. create_user accepts a raw dict and trusts it.
     Missing/blank `name` or `email`, or a malformed email, are silently
     accepted. There is no Pydantic request model.
  2. BLOCKING CALLS IN ASYNC HANDLERS. Handlers are `async def` but call
     the synchronous db.* functions directly, blocking the event loop.
  3. WRONG STATUS CODES. Creating a resource returns 200 instead of 201;
     a missing user returns 200 with null instead of 404.

Expected refactor outcome (what the tests below pin):
  * Add a Pydantic `UserCreate` model that rejects blank name and
    invalid email with HTTP 422.
  * Return 201 on create, 404 on missing user.
  * (Paired with db.py becoming async) await the data layer.

The tests in tests/ FAIL against this file as written and PASS once the
debt is fixed. The harness must make them pass.
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import db

app = FastAPI(title="Debt Demo API")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/users")
async def get_users():
    # DEBT: blocking sync call inside an async handler.
    return db.list_users()


@app.get("/users/{user_id}")
async def read_user(user_id: int):
    # DEBT: blocking sync call; also returns 200 + null for a missing user
    # instead of a proper 404.
    user = db.get_user(user_id)
    return user


@app.post("/users")
async def create_user(payload: dict):
    # DEBT: no validation at all. Accepts anything. Returns 200 not 201.
    name = payload.get("name")
    email = payload.get("email")
    # blocking sync call inside async handler
    created = db.insert_user(name, email)
    return created


@app.delete("/users/{user_id}")
async def remove_user(user_id: int):
    # DEBT: blocking sync call; returns 200 even when nothing was deleted.
    deleted = db.delete_user(user_id)
    return {"deleted": deleted}
