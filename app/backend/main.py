from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import os

from app.backend.routes import api_router
from app.backend.database.connection import engine
from app.backend.database.models import Base
from app.backend.services.ollama_service import ollama_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Alpha Terminal API",
    description="Backend API for Alpha Terminal — retail-investor research terminal.",
    version="1.0.0",
)


def _check_required_keys() -> None:
    """Log a loud, actionable warning when mandatory API keys are missing.

    Without these the app still boots, but agents silently degrade to
    "no edge" / empty data, which looks like a bug to a new user. We warn at
    startup instead so the cause is obvious. We never log the key values.
    """
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        logger.warning(
            "DEEPSEEK_API_KEY is not set — the LLM agents cannot run. "
            "Add it to your .env (see README 'Quick start'). The dashboard will "
            "load but scans, theses, and chat will fail."
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

# Initialize database tables (this is safe to run multiple times)
Base.metadata.create_all(bind=engine)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Frontend URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routes
app.include_router(api_router)

@app.on_event("startup")
async def startup_event():
    """Startup checks: required API keys, then Ollama availability."""
    _check_required_keys()
    try:
        logger.info("Checking Ollama availability...")
        status = await ollama_service.check_ollama_status()
        
        if status["installed"]:
            if status["running"]:
                logger.info(f"✓ Ollama is installed and running at {status['server_url']}")
                if status["available_models"]:
                    logger.info(f"✓ Available models: {', '.join(status['available_models'])}")
                else:
                    logger.info("ℹ No models are currently downloaded")
            else:
                logger.info("ℹ Ollama is installed but not running")
                logger.info("ℹ You can start it from the Settings page or manually with 'ollama serve'")
        else:
            logger.info("ℹ Ollama is not installed. Install it to use local models.")
            logger.info("ℹ Visit https://ollama.com to download and install Ollama")
            
    except Exception as e:
        logger.warning(f"Could not check Ollama status: {e}")
        logger.info("ℹ Ollama integration is available if you install it later")
