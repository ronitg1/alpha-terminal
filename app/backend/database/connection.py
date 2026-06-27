from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os
from pathlib import Path

# Get the backend directory path
BACKEND_DIR = Path(__file__).parent.parent
DATABASE_PATH = BACKEND_DIR / "hedge_fund.db"

# Database URL: prefer DATABASE_URL from the environment (set by the host —
# e.g. Railway/Render Postgres) so cloud deploys use a managed database; fall
# back to a local SQLite file for local development when it's unset.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip() or f"sqlite:///{DATABASE_PATH}"
# Some providers hand out the legacy "postgres://" scheme; SQLAlchemy 2 needs
# "postgresql://". Normalize so either form works.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_is_sqlite = DATABASE_URL.startswith("sqlite")

# Create SQLAlchemy engine. check_same_thread is a SQLite-only arg; Postgres
# gets pool_pre_ping so dropped connections (idle timeouts on managed DBs) are
# transparently recycled.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class for models
Base = declarative_base()

# Dependency for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close() 