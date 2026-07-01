from fastapi import APIRouter, Depends

from app.backend.auth import get_current_user_id
from app.backend.routes.access import router as access_router
from app.backend.routes.auth import router as auth_router
from app.backend.routes.hedge_fund import router as hedge_fund_router
from app.backend.routes.health import router as health_router
from app.backend.routes.storage import router as storage_router
from app.backend.routes.flows import router as flows_router
from app.backend.routes.flow_runs import router as flow_runs_router
from app.backend.routes.ollama import router as ollama_router
from app.backend.routes.language_models import router as language_models_router
from app.backend.routes.api_keys import router as api_keys_router
from app.backend.routes.sleeves import router as sleeves_router
from app.backend.routes.patterns import router as patterns_router
from app.backend.routes.news import router as news_router
from app.backend.routes.transcripts import router as transcripts_router
from app.backend.routes.pnl import router as pnl_router
from app.backend.routes.user_settings import router as user_settings_router
from app.backend.routes.robinhood import router as robinhood_router
from app.backend.routes.snaptrade import router as snaptrade_router
from app.backend.routes.portfolio import router as portfolio_router
from app.backend.routes.market import router as market_router
from app.backend.routes.scheduled import router as scheduled_router

# Main API router
api_router = APIRouter()

# Per-user data routers require an authenticated user when AUTH_ENABLED is on.
# `get_current_user_id` is the enforcement point: with auth OFF it returns the
# default user (no 401), so this is dormant and safe until the flag is flipped;
# with auth ON, an unauthenticated request to any of these routes gets a 401
# before the handler runs. Applied at the router level so every current and
# future route under these prefixes is covered without per-handler wiring.
_AUTH = [Depends(get_current_user_id)]

# Include sub-routers
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router, tags=["auth"])
api_router.include_router(hedge_fund_router, tags=["hedge-fund"], dependencies=_AUTH)
api_router.include_router(storage_router, tags=["storage"])
api_router.include_router(flows_router, tags=["flows"], dependencies=_AUTH)
api_router.include_router(flow_runs_router, tags=["flow-runs"], dependencies=_AUTH)
api_router.include_router(ollama_router, tags=["ollama"])
api_router.include_router(language_models_router, tags=["language-models"])
# api_keys is user-scoped + encrypted in Phase 3 step 3; gated now so it is never
# reachable unauthenticated once auth is on. NOTE: flows/flow_runs/api_keys still
# use the legacy single-tenant tables (no user_id column) — they are globally
# shared across users until step 3 migrates them. Tracked in HANDOFF.
api_router.include_router(api_keys_router, tags=["api-keys"], dependencies=_AUTH)
api_router.include_router(user_settings_router, tags=["user-settings"], dependencies=_AUTH)
api_router.include_router(sleeves_router, tags=["sleeves"], dependencies=_AUTH)
api_router.include_router(patterns_router, tags=["patterns"], dependencies=_AUTH)
api_router.include_router(news_router, tags=["news"], dependencies=_AUTH)
api_router.include_router(transcripts_router, tags=["transcripts"], dependencies=_AUTH)
api_router.include_router(pnl_router, dependencies=_AUTH)
api_router.include_router(robinhood_router, tags=["robinhood"], dependencies=_AUTH)
api_router.include_router(snaptrade_router, tags=["snaptrade"], dependencies=_AUTH)
api_router.include_router(portfolio_router, tags=["portfolio"], dependencies=_AUTH)
api_router.include_router(market_router, tags=["market"], dependencies=_AUTH)
api_router.include_router(access_router, tags=["access"], dependencies=_AUTH)
# Scheduled pre-scans: NOT router-level _AUTH — the user CRUD routes carry their
# own get_current_user_id dependency, while /scheduled/run-due is reached by the
# external scheduler and is guarded by the shared CRON_SECRET instead.
api_router.include_router(scheduled_router, tags=["scheduled"])
