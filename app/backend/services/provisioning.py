"""First-login user provisioning (Phase 3, step 5).

The first time an authenticated user is seen, we create their ``users`` row and
then either:

- **Claim the existing data** — if this user is the configured owner, reassign
  every ``default``-owned row (portfolios, watchlists, settings, theses, P&L,
  scans, API keys) to them. This is the one-time migration that turns the shared
  pre-auth instance into the owner's personal account.

  Owner identity is established **securely** by either:
    * ``OWNER_USER_ID`` — the owner's Clerk ``sub`` (the verified token subject;
      unspoofable). Preferred. Bootstrap: enable auth, log in once as the owner,
      read the ``sub`` (logged below / the ``users`` table), set this var.
    * ``OWNER_EMAIL`` — matched only against a **verified** email
      (``email_verified`` true in the token). An attacker on open signup cannot
      set ``email_verified`` for an address they don't control, so they cannot
      claim the owner's data (which includes the encrypted BYOK API keys). An
      unverified email never claims.

  Because the claim only fires for one specific owner identity and only on that
  owner's first login (their ``users`` row is absent), it is inherently one-time:
  no other account can trigger it, before or after.
- **Seed a starter** — otherwise the new user gets a small generic starter
  portfolio and a default cash-reserve setting so their dashboard is usable
  immediately.

Idempotent and concurrency-safe: provisioning only runs when the ``users`` row
doesn't yet exist (a second concurrent first-request loses the PK insert race and
is treated as already-provisioned). An in-process cache skips the DB check on
every subsequent request. Entirely dormant when auth is off — ``get_current_user_id``
only calls this for an authenticated user under the DB backend.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy.exc import IntegrityError

from app.backend.database.app_models import (
    DEFAULT_CASH_RESERVE_PCT,
    DEFAULT_USER_ID,
    Portfolio,
    PortfolioSetting,
    PnlPosition,
    ScanResult,
    Thesis,
    User,
    UserSettings,
    Watchlist,
)
from app.backend.database.models import ApiKey
from app.backend.services._storage import session_scope

logger = logging.getLogger(__name__)

__all__ = ["ensure_provisioned", "owner_email", "owner_user_id"]

# Models carrying a ``user_id`` that are reassigned to the owner on claim.
_OWNED_MODELS = [
    UserSettings,
    Portfolio,
    Watchlist,
    PortfolioSetting,
    PnlPosition,
    Thesis,
    ScanResult,
    ApiKey,
]

# A minimal, generic starter portfolio for brand-new (non-owner) users.
_STARTER_NAME = "starter"
_STARTER_AGENTS = ["alpha_seeker"]
_STARTER_WEIGHTS = {"alpha_seeker": 1.0}
_STARTER_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]

# Per-process memo of users already provisioned, so the DB existence check runs
# at most once per user per process.
_provisioned: set[str] = set()


def owner_email() -> str | None:
    """The configured owner email (``OWNER_EMAIL``), normalized, or None."""
    value = os.environ.get("OWNER_EMAIL", "").strip().lower()
    return value or None


def owner_user_id() -> str | None:
    """The configured owner Clerk ``sub`` (``OWNER_USER_ID``), or None."""
    value = os.environ.get("OWNER_USER_ID", "").strip()
    return value or None


def _is_owner(user_id: str, email: str | None, email_verified: bool) -> bool:
    """Whether this user is the configured owner who may claim the default data.

    Two secure paths: an exact Clerk ``sub`` match (unspoofable), or a
    ``OWNER_EMAIL`` match that is ONLY honored for a **verified** email — so an
    attacker cannot claim by presenting an unverified address they don't own."""
    owner_sub = owner_user_id()
    if owner_sub and user_id == owner_sub:
        return True
    configured_email = owner_email()
    if configured_email and email and email_verified and email == configured_email:
        return True
    return False


def ensure_provisioned(user_id: str, email: str | None = None, email_verified: bool = False) -> None:
    """Provision ``user_id`` on first sight (create row, claim-or-seed). No-op if
    already provisioned or for the sentinel default user. Never raises — a
    provisioning failure is logged and the request proceeds."""
    if user_id in _provisioned or user_id == DEFAULT_USER_ID:
        return
    try:
        with session_scope() as db:
            if db.get(User, user_id) is not None:
                _provisioned.add(user_id)
                return

            db.add(User(id=user_id, email=email))
            db.flush()  # surface a PK collision (concurrent first request) early

            if _is_owner(user_id, email, email_verified):
                moved = _claim_default_data(db, user_id)
                if moved:
                    logger.info("Owner first login: claimed %d default rows for user %s.", moved, user_id)
                else:
                    # Owner matched but there was nothing to claim — already
                    # claimed, or a misconfigured owner. Give them a starter so
                    # they don't land on an empty dashboard, and flag it.
                    logger.warning("Owner %s matched but no default data to claim; seeding starter.", user_id)
                    _seed_starter(db, user_id)
            else:
                _seed_starter(db, user_id)
                logger.info("Seeded starter portfolio for new user %s.", user_id)

            db.commit()
        _provisioned.add(user_id)
    except IntegrityError:
        # Another concurrent request provisioned this user first — fine.
        _provisioned.add(user_id)
    except Exception as exc:  # never break the request over provisioning
        logger.warning("Provisioning failed for user %s: %s", user_id, exc)


def _claim_default_data(db, user_id: str) -> int:
    """Reassign every ``default``-owned row to ``user_id``; return rows moved.

    ``synchronize_session=False`` is safe here: we never read the moved rows in
    this session after the bulk update — we commit and close immediately."""
    moved = 0
    for model in _OWNED_MODELS:
        moved += db.query(model).filter(model.user_id == DEFAULT_USER_ID).update(
            {model.user_id: user_id}, synchronize_session=False
        )
    return moved


def _seed_starter(db, user_id: str) -> None:
    """Give a new user a usable starting point."""
    db.add(UserSettings(user_id=user_id, cash_reserve_pct=DEFAULT_CASH_RESERVE_PCT))
    db.add(
        Portfolio(
            user_id=user_id,
            name=_STARTER_NAME,
            allocation_pct=100.0,
            agents=list(_STARTER_AGENTS),
            agent_weights=dict(_STARTER_WEIGHTS),
            tickers=list(_STARTER_TICKERS),
        )
    )
