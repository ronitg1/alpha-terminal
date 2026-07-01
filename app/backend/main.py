from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import os

from app.backend.routes import api_router
from app.backend.database.connection import engine, _is_sqlite
from app.backend.database.models import Base
from app.backend.services.key_resolver import MissingUserKey
from app.backend.services.ollama_service import ollama_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# httpx/httpcore log every request URL at INFO. Any client that carries
# credentials in a query param would leak them into the server log, so these
# stay at WARNING regardless of the app's own log level.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup checks: auth/encryption invariant, required API keys, then Ollama."""
    _check_auth_encryption()
    _check_required_keys()
    # The Ollama probe is for local-model users; on a cloud box with no Ollama
    # it just wastes startup time on a connection attempt. Skip it when
    # SKIP_OLLAMA_CHECK is set (recommended in container deploys).
    if os.environ.get("SKIP_OLLAMA_CHECK", "").strip().lower() in ("1", "true", "yes"):
        logger.info("Skipping Ollama check (SKIP_OLLAMA_CHECK set).")
        yield
        return
    try:
        logger.info("Checking Ollama availability...")
        status = await ollama_service.check_ollama_status()

        if status["installed"]:
            if status["running"]:
                logger.info(f"Ollama is installed and running at {status['server_url']}")
                if status["available_models"]:
                    logger.info(f"Available models: {', '.join(status['available_models'])}")
                else:
                    logger.info("No Ollama models are currently downloaded")
            else:
                logger.info("Ollama is installed but not running (optional — start with 'ollama serve')")
        else:
            logger.info("Ollama is not installed (optional — needed only for local models)")

    except Exception as e:
        logger.warning(f"Could not check Ollama status: {e}")

    yield


app = FastAPI(
    title="Alpha Terminal API",
    description="Backend API for Alpha Terminal — retail-investor research terminal.",
    version="1.11.15",
    lifespan=_lifespan,
)


def _check_auth_encryption() -> None:
    """Refuse to boot if auth is on but secret encryption isn't configured.

    With ``AUTH_ENABLED`` on, users store their own provider keys (BYOK), which
    are encrypted at rest with ``API_KEY_ENCRYPTION_KEY``. If that key is missing,
    every key save would 500 at request time — so fail loudly at startup instead
    (the repo convention: validate config at boot, not two hours into a request).
    Dormant when auth is off, so local and the current cloud deploy are unaffected.
    """
    from app.backend.auth import auth_enabled
    from app.backend.crypto import encryption_configured

    if auth_enabled() and not encryption_configured():
        raise RuntimeError(
            "AUTH_ENABLED is on but API_KEY_ENCRYPTION_KEY is not set. Per-user API "
            "keys cannot be encrypted/stored. Set API_KEY_ENCRYPTION_KEY (generate one "
            "with `python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`) "
            "before enabling auth."
        )


def _check_required_keys() -> None:
    """Log a loud, actionable warning when mandatory API keys are missing.

    Without these the app still boots, but agents silently degrade to
    "no edge" / empty data, which looks like a bug to a new user. We warn at
    startup instead so the cause is obvious. We never log the key values.
    """
    has_deepseek = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
    has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    if not has_deepseek and not has_openrouter:
        logger.warning(
            "No LLM key found (DEEPSEEK_API_KEY or OPENROUTER_API_KEY). "
            "Add one to your .env or save a user key in Settings. The dashboard "
            "will load but scans, theses, and chat will fail until a key exists."
        )

    has_massive = bool(os.environ.get("MASSIVE_API_KEY", "").strip())
    has_fds = bool(os.environ.get("FINANCIAL_DATASETS_API_KEY", "").strip())
    if not has_massive and not has_fds:
        logger.warning(
            "No market-data key found (MASSIVE_API_KEY or FINANCIAL_DATASETS_API_KEY). "
            "Prices, fundamentals, and pattern scans will return empty. "
            "Set MASSIVE_API_KEY (Polygon) in your .env."
        )

    if not os.environ.get("FINNHUB_API_KEY", "").strip():
        logger.info(
            "FINNHUB_API_KEY is not set (optional). The Market News tab and the "
            "insider / growth-ratio fallbacks will be unavailable; everything else works."
        )

# Initialize database tables. On SQLite (local dev) we create_all for zero-setup
# convenience. On a managed Postgres, Alembic owns the schema — running
# create_all there would race the migrations (it creates tables Alembic then
# tries to CREATE again, and skips the migration's seed rows), so we skip it and
# rely on `alembic upgrade head` in the deploy step (see DEPLOY.md / railway.toml).
if _is_sqlite:
    Base.metadata.create_all(bind=engine)

# Configure CORS. Origins come from the ALLOWED_ORIGINS env var (comma-
# separated) so the deployed frontend's domain can be whitelisted without a
# code change; it defaults to the local Vite dev server so local development is
# unchanged. In production set e.g.
#   ALLOWED_ORIGINS=https://your-app.vercel.app,https://app.yourdomain.com
_DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
_allowed_origins = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if o.strip()
]

# Browsers reject `allow_credentials=True` together with a `*` wildcard origin,
# and it would silently break every credentialed request. If someone sets
# ALLOWED_ORIGINS=*, honor the wildcard but turn credentials off and say so.
_allow_credentials = True
if "*" in _allowed_origins:
    logger.warning(
        "ALLOWED_ORIGINS contains '*' — disabling allow_credentials (the two are "
        "incompatible). Set explicit origins to allow credentialed requests."
    )
    _allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Bind the current-request user for per-user data isolation (Phase 3). This
# middleware never raises (it only records the auth result and binds the context
# var; the get_current_user_id dependency enforces 401/500), and CORS handles its
# own preflight, so the relative order is immaterial. Dormant when AUTH_ENABLED is
# off — it binds the default user, a no-op vs. the context-var default.
from app.backend.middleware import UserContextMiddleware  # noqa: E402

app.add_middleware(UserContextMiddleware)


@app.exception_handler(MissingUserKey)
async def _missing_user_key_handler(_request: Request, exc: MissingUserKey) -> JSONResponse:
    """A required per-user API key isn't set (e.g. DeepSeek for a thesis). Return a
    clean 402 the frontend can soft-gate to Settings, instead of an unhandled 500.
    Providers with their own graceful handling (e.g. Robinhood) catch this earlier."""
    return JSONResponse(
        status_code=402,
        content={"detail": f"Add your {exc.provider} API key in Settings to use this feature.", "provider": exc.provider},
    )


@app.get("/health")
def health() -> dict:
    """Liveness probe — no DB or external calls, so platform health checks
    (Railway/Render) don't flap when a data provider is slow."""
    return {"ok": True, "service": "alpha-terminal-api", "version": app.version}


# Include all routes
app.include_router(api_router)
