"""Jira Dashboard API — standalone FastAPI service.

Entry point for the jira-api microservice. Validates JWT tokens
issued by the main backend, serves dashboard data from MongoDB,
and proxies issue operations to Jira Cloud/Server.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
    _has_slowapi = True
except ImportError:
    _has_slowapi = False

from src.auth import init_auth
from src.config import Config
from src.db import close_db, connect_db
from src.routes.dashboard import router as dashboard_router
from src.routes.issues import init_issue_routes
from src.routes.issues import router as issues_router
from src.routes.portfolio import init_portfolio_routes
from src.routes.portfolio import router as portfolio_router
from src.routes.sync import cleanup_stale_sync_progress, init_sync_routes
from src.routes.sync import router as sync_router
from src.services.gcs import GCSClient
from src.services.jira_client import JiraClient
from src.services.jira_sync import JiraSyncService
from src.services.rollup_engine import RollupEngine
from src.services.snapshot_service import SnapshotService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Load config
config_path = os.environ.get("CONFIG_PATH", "config/default.json")
config = Config(config_path)

_root = config.get("server.root_path", "").rstrip("/")
API_PREFIX = f"{_root}/api/v1"
SERVICE_NAME = "jira-api"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — connect DB and initialize services on startup."""
    # Startup
    await connect_db(config)
    init_auth(config)

    jira_client = JiraClient(config)
    gcs_client = GCSClient(config)
    rollup_engine = RollupEngine(config)
    sync_service = JiraSyncService(config, jira_client, gcs_client, rollup_engine=rollup_engine)

    init_sync_routes(sync_service)
    init_issue_routes(jira_client, config)
    init_portfolio_routes(rollup_engine, SnapshotService(), config)

    # Clean up any sync progress stuck from a previous server crash
    await cleanup_stale_sync_progress()

    # Log Jira config status (don't validate connectivity — it blocks the event loop)
    jira_base = config.get("jira.base_url", "")
    if not jira_base:
        logger.warning("jira.base_url is empty — Jira features will be unavailable")
    else:
        logger.info("Jira configured: %s (connectivity validated on first use)", jira_base)

    logger.info("%s started", SERVICE_NAME)
    yield

    # Shutdown
    await close_db()
    logger.info("%s stopped", SERVICE_NAME)


app = FastAPI(
    title="Jira Dashboard API",
    description="Standalone API service for Jira dashboard data, sync, and issue management.",
    version="1.0.0",
    root_path="",  # prefix is applied directly to route paths via _root
    lifespan=lifespan,
)

# Rate limiting
if _has_slowapi:
    limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    logger.info("Rate limiting enabled: 60 requests/minute per IP")
else:
    logger.warning("slowapi not installed — rate limiting disabled")

# CORS
cors_origins = config.get("server.cors_origins", ["http://localhost:3000", "http://localhost:5173"])
if isinstance(cors_origins, str):
    cors_origins = [o.strip() for o in cors_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# Routers
app.include_router(dashboard_router, prefix=API_PREFIX)
app.include_router(sync_router, prefix=API_PREFIX)
app.include_router(issues_router, prefix=API_PREFIX)
app.include_router(portfolio_router, prefix=API_PREFIX)


@app.get(f"{_root}/health/live", tags=["health"])
async def health_live():
    """Liveness probe."""
    return {"status": "ok", "service": SERVICE_NAME}


@app.get(f"{_root}/health/ready", tags=["health"])
async def health_ready():
    """Readiness probe — checks DB connectivity."""
    try:
        from src.db import get_db
        db = get_db()
        await db.command("ping")
        return {"status": "ok", "service": SERVICE_NAME, "database": "connected"}
    except Exception as exc:
        return {"status": "degraded", "service": SERVICE_NAME, "database": str(exc)}
