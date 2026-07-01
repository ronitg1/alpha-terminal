"""Multi-tenant domain models — the Postgres home for application state that
currently lives in JSON files (`app/data/*.json`) and Python config modules
(`src/config/portfolio_config.py`, `src/config/watchlist.py`).

Every row is scoped to a ``user_id`` so one deployment can serve many users.
Until real authentication lands (Phase 3 — Clerk), all rows are owned by
:data:`DEFAULT_USER_ID`; the repositories already take a ``user_id`` argument so
wiring auth later is a parameter change, not a schema change.

Design notes:
- Column style mirrors the existing :mod:`app.backend.database.models` (JSON
  columns for structured blobs, timezone-aware ``server_default=func.now()``
  timestamps) so the two model files read consistently.
- Shapes are **1:1 with the file stores** they replace (see the service modules
  in ``app/backend/services``) so the repositories can return the exact dicts
  the routes already expect — no wire-format change for the frontend.
- These models attach to the shared :class:`Base`; importing this module (done
  by ``app.backend.database.__init__``) registers them on ``Base.metadata`` for
  both ``create_all`` and Alembic autogenerate.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList

from app.backend.database.connection import Base

# SQLAlchemy does NOT track in-place mutation of a plain JSON column (e.g.
# ``row.tickers.append(x)`` won't be saved). Wrapping the column type in
# MutableList/MutableDict makes such edits dirty the row so a later commit
# persists them — removes a silent data-loss footgun at cutover. The
# repositories still prefer whole-value reassignment, but this is defense in
# depth.
_JSONList = MutableList.as_mutable(JSON)
_JSONDict = MutableDict.as_mutable(JSON)

# Owner of every row until Clerk auth is wired in Phase 3. Repositories default
# their ``user_id`` argument to this, so today's single-tenant behavior is
# preserved while the schema is already multi-tenant.
DEFAULT_USER_ID = "default"

# Default cash-reserve floor for a new user (mirrors CASH_RESERVE_PCT in
# src/config/portfolio_config.py). Single source for the DB layer's default so
# the model, the seed, and the repository fallback don't drift.
DEFAULT_CASH_RESERVE_PCT = 10.0
DEFAULT_LLM_MODEL_PROVIDER = "DeepSeek"
DEFAULT_LLM_MODEL_NAME = "deepseek-reasoner"


class User(Base):
    """An account. ``id`` will be the Clerk user id once auth lands; until then
    a single row with id == DEFAULT_USER_ID owns all data."""

    __tablename__ = "users"

    id = Column(String(255), primary_key=True)
    email = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserSettings(Base):
    """Per-user portfolio-level settings (currently just the cash-reserve floor,
    which lived as the module-level ``CASH_RESERVE_PCT``)."""

    __tablename__ = "user_settings"

    user_id = Column(String(255), primary_key=True)
    cash_reserve_pct = Column(Float, nullable=False, default=DEFAULT_CASH_RESERVE_PCT)
    # Whether the user has finished (or skipped) the first-login onboarding
    # walkthrough. Server-side source of truth so it's once-per-account forever,
    # surviving a browser/localStorage clear or a new device.
    onboarding_completed = Column(Boolean, nullable=False, default=False, server_default=func.false())
    llm_model_provider = Column(String(64), nullable=False, default=DEFAULT_LLM_MODEL_PROVIDER, server_default=DEFAULT_LLM_MODEL_PROVIDER)
    llm_model_name = Column(String(200), nullable=False, default=DEFAULT_LLM_MODEL_NAME, server_default=DEFAULT_LLM_MODEL_NAME)
    llm_preference_saved = Column(Boolean, nullable=False, default=False, server_default=func.false())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Portfolio(Base):
    """A "sleeve" — replaces an entry in ``PORTFOLIO_SLEEVES``. The agents,
    weights, and tickers keep their original JSON shape."""

    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    allocation_pct = Column(Float, nullable=False, default=0.0)
    agents = Column(_JSONList, nullable=False, default=list)            # list[str]
    agent_weights = Column(_JSONDict, nullable=False, default=dict)     # dict[str, float]
    tickers = Column(_JSONList, nullable=False, default=list)           # list[str]
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_portfolio_user_name"),
    )


class Watchlist(Base):
    """A named watchlist — replaces one entry in ``watchlists.json``. Entries
    keep the ``[{ticker, comment}]`` shape as a JSON blob."""

    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    entries = Column(_JSONList, nullable=False, default=list)      # list[{ticker, comment}]
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),
    )


class PortfolioSetting(Base):
    """Per-sleeve, per-ticker override — replaces ``portfolio_settings.json``.
    ``agents`` of None means "inherit the sleeve's default agents"."""

    __tablename__ = "portfolio_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    sleeve_name = Column(String(64), nullable=False)
    ticker = Column(String(16), nullable=False)
    allocation_pct = Column(Float, nullable=False, default=0.0)
    agents = Column(_JSONList, nullable=True)                     # list[str] | None
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "sleeve_name", "ticker", name="uq_psetting_user_sleeve_ticker"),
    )


class PnlPosition(Base):
    """A tracked P&L position — replaces a record in ``pnl_positions.json``.
    Prices are stored PER SHARE (the 100x option multiplier is applied in
    code, never baked in), matching the file store. ``created_at`` /
    ``updated_at`` stay ISO strings to preserve the exact API the routes
    return."""

    __tablename__ = "pnl_positions"

    id = Column(String(32), primary_key=True)                    # "pos_ab12cd34"
    user_id = Column(String(255), nullable=False, index=True)
    kind = Column(String(16), nullable=False)                    # "option" | "stock"
    ticker = Column(String(16), nullable=False)
    side = Column(String(8), nullable=False)                     # "long" | "short"
    qty = Column(Float, nullable=False)
    option = Column(_JSONDict, nullable=True)                    # {type, strike, expiration, contract_ticker} | None
    entry_price = Column(Float, nullable=True)
    entry_date = Column(String(10), nullable=True)               # YYYY-MM-DD
    status = Column(String(8), nullable=False, default="open")   # "open" | "closed"
    exit_price = Column(Float, nullable=True)
    exit_date = Column(String(10), nullable=True)
    source = Column(String(16), nullable=False, default="manual")  # manual|screener|pattern|fidelity
    real = Column(Boolean, nullable=False, default=False)        # True = actual fill, False = paper
    notes = Column(Text, nullable=False, default="")
    import_key = Column(String(255), nullable=True, index=True)
    closing_import_key = Column(String(255), nullable=True)
    created_at = Column(String(40), nullable=True)               # ISO 8601 string (mirrors file shape)
    updated_at = Column(String(40), nullable=True)


class Thesis(Base):
    """A saved LLM thesis — replaces an entry in ``theses.json``. ``key`` is the
    scope string ('portfolio' | 'sleeve:<name>' | 'ticker:<SYM>:<depth>'); the
    full payload (including its own ``saved_at``) is kept as JSON."""

    __tablename__ = "theses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    key = Column(String(128), nullable=False)
    payload = Column(_JSONDict, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_thesis_user_key"),
    )


class ScanResult(Base):
    """A morning-scan result for one date — replaces the
    ``outputs/YYYY-MM-DD_morning_scan.json`` sidecar. The full UI payload
    ({date, rows, ...}) is stored as JSON; one row per (user, date)."""

    __tablename__ = "scan_results"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    scan_date = Column(String(10), nullable=False)               # YYYY-MM-DD
    payload = Column(_JSONDict, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "scan_date", name="uq_scan_user_date"),
    )


class AccessRequest(Base):
    """A user's request to use the owner's shared market-data keys for free
    (Phase 3). The owner approves/denies; an approved row grants the requester's
    email shared-key access in addition to the static ``SHARED_DATA_EMAILS`` env
    allowlist. One row per requester (``user_id``)."""

    __tablename__ = "access_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False)          # requester's Clerk sub
    email = Column(String(320), nullable=True, index=True)  # requester's email (for the grant match)
    status = Column(String(16), nullable=False, default="pending")  # pending | approved | denied
    note = Column(Text, nullable=True)                     # optional message to the owner
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_access_request_user"),
    )


class ScanSchedule(Base):
    """A user's scheduled time for an automatic background pattern scan, so the
    Pattern Scanner is pre-computed and ready when they open it. One row per
    (user, time): a user can schedule several times a day. ``last_run_on`` is the
    YYYY-MM-DD (in ``timezone``) the schedule last fired, used to de-duplicate so
    a 15-minute trigger can't run the same slot twice in a day."""

    __tablename__ = "scan_schedules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    time_of_day = Column(String(5), nullable=False)            # "HH:MM" 24-hour, local to timezone
    timezone = Column(String(64), nullable=False, default="America/New_York")  # IANA tz
    enabled = Column(Boolean, nullable=False, default=True, server_default=func.true())
    last_run_on = Column(String(10), nullable=True)            # YYYY-MM-DD in `timezone`, dedupe
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "time_of_day", name="uq_scan_schedule_user_time"),
    )


class PrescanResult(Base):
    """The latest pre-computed pattern-scan results for a user (one row per user),
    written by the scheduled background scan and read by the Pattern Scanner so it
    loads instantly. ``results`` is the same list[ScanResult-dict] the live
    ``/patterns/scan`` returns."""

    __tablename__ = "prescan_results"

    user_id = Column(String(255), primary_key=True)
    results = Column(_JSONList, nullable=False, default=list)  # list[pattern scan result dict]
    timeframe = Column(String(8), nullable=False, default="day")
    ticker_count = Column(Integer, nullable=False, default=0)
    computed_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
