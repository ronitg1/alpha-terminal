"""Storage-backend dispatch helper for the file services (Phase 2 cutover).

``STORAGE_BACKEND`` selects where the application stores live (per-user) state:

- ``"file"`` (default): today's local JSON/config files. The local app is
  100% unchanged — this is the behavior every existing install already has.
- ``"db"``: the multi-tenant Postgres repositories in
  ``app.backend.repositories``.

The flag defaults to ``"file"`` and is read **at call time** (not import time)
so a deploy can flip it via the environment and tests can toggle it per-case.
Until Clerk auth lands (Phase 3), every row is owned by :data:`DEFAULT_USER_ID`.

Why a contextmanager rather than ``Depends(get_db)``: the file services are
plain functions called from many places (routes, the scan engine, tests), not
just FastAPI request handlers. They open a short-lived session, do one unit of
work, and close it — see :func:`session_scope`. Routes that already have a
request-scoped session keep using ``Depends(get_db)`` directly.

Cutover recipe (follow this for every service so all stores stay consistent):

1. At the top of each public function::

       if use_db():
           with session_scope() as db:                 # add integrity_as_value_error()
               return Repo(db, DEFAULT_USER_ID).method(...)  # for mutating calls that
       ...                                              # can hit a unique constraint
       # fall through to the original file implementation unchanged

2. Wrap inserts/renames that can violate a unique constraint in
   :func:`integrity_as_value_error` so a race becomes a clean ``ValueError``.
   Omit it for updates/deletes that keep the key unchanged (they can't collide)
   — and leave a one-line comment saying why, so the next service is consistent.
3. The **route** (or service, where it already raises HTTPException) owns HTTP
   status mapping: catch ``ValueError`` and return **409** for a name/uniqueness
   conflict, **400** for bad input; catch ``LookupError`` for **404**. This seam
   does NOT map status codes for you — it only normalizes the exception type.
4. Return the canonical post-write read shape from BOTH backends (the repo's
   mutating methods already return their ``read_*`` snapshot; the file path
   returns its own read) so the two never drift.
5. Fresh-DB seeding: if a store is empty on a brand-new Postgres but the app
   needs shipped defaults (e.g. portfolios), seed them in an Alembic data
   migration (idempotent), not lazily on read.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Imported lazily-but-module-level so tests can monkeypatch
# ``app.backend.services._storage.SessionLocal`` onto an in-memory engine.
from app.backend.database import SessionLocal
from app.backend.database.app_models import DEFAULT_USER_ID

__all__ = [
    "DEFAULT_USER_ID",
    "use_db",
    "storage_backend",
    "session_scope",
    "integrity_as_value_error",
]


def storage_backend() -> str:
    """The active backend name, normalized. ``"file"`` unless explicitly set to
    ``"db"`` (anything else falls back to ``"file"`` — fail safe, not loud)."""
    value = os.environ.get("STORAGE_BACKEND", "file").strip().lower()
    return "db" if value == "db" else "file"


def use_db() -> bool:
    """True when the Postgres repositories should back the file services."""
    return storage_backend() == "db"


@contextmanager
def session_scope() -> Iterator[Session]:
    """A short-lived session for a single unit of work in a non-route service.

    The repositories commit themselves, so on the happy path this just releases
    the connection. On an exception we ``rollback()`` explicitly before closing:
    if a repo mutates (``add``/``flush``) and then raises before its commit, the
    half-done transaction is discarded deterministically rather than relying on
    ``close()``'s implicit rollback. ``close()`` always runs in ``finally``."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def integrity_as_value_error() -> Iterator[None]:
    """Translate a DB unique-constraint violation into a domain ``ValueError``.

    The repositories check-then-insert, which is race-prone: two concurrent
    inserts of the same (user, name) can both pass the check and one then trips
    the unique constraint on commit, raising ``IntegrityError``. Routes already
    map ``ValueError`` to a clean 409, so normalize it here at the cutover seam
    rather than leaking a 500."""
    try:
        yield
    except IntegrityError as exc:
        raise ValueError(str(getattr(exc, "orig", exc))) from exc
