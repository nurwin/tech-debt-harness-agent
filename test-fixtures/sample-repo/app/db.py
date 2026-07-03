"""
Data-access layer.

ARCHITECTURAL DEBT (intentional — this is the target of the refactor):
  * Every function is SYNCHRONOUS and calls time.sleep() to simulate a
    blocking driver. When called from the async FastAPI handlers in
    api.py, these block the event loop.
  * A global module-level dict is used as the "database" with no locking.

The harness is expected to convert these to async (async def + await
asyncio.sleep) so the event loop is no longer blocked, WITHOUT changing
their observable return values.
"""
import time

# In-memory "database". Pre-seeded so reads have something to return.
_USERS: dict[int, dict] = {
    1: {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com"},
}
_NEXT_ID = 2


def _simulate_io() -> None:
    # Stand-in for a blocking network/disk round-trip.
    time.sleep(0.01)


def get_user(user_id: int) -> dict | None:
    """Fetch a single user by id, or None if absent."""
    _simulate_io()
    return _USERS.get(user_id)


def list_users() -> list[dict]:
    """Return all users."""
    _simulate_io()
    return list(_USERS.values())


def insert_user(name: str, email: str) -> dict:
    """Insert a new user and return the created record."""
    global _NEXT_ID
    _simulate_io()
    record = {"id": _NEXT_ID, "name": name, "email": email}
    _USERS[_NEXT_ID] = record
    _NEXT_ID += 1
    return record


def delete_user(user_id: int) -> bool:
    """Delete a user; return True if it existed."""
    _simulate_io()
    return _USERS.pop(user_id, None) is not None


def _reset_for_tests() -> None:
    """Test helper: restore pristine seed state."""
    global _USERS, _NEXT_ID
    _USERS = {1: {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com"}}
    _NEXT_ID = 2
