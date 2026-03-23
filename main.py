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

from src.auth import init_auth
from src.config import Config
from src.db import close_db, connect_db
from src.routes.dashboard import router as dashboard_router
from src.routes.issues import init_issue_routes
from src.routes.issues import router as issues_router
from src.routes.sync import init_sync_routes
from src.routes.sync import router as sync_router
from src.services.gcs import GCSClient
from src.services.jira_client import JiraClient
from src.services.jira_sync import JiraSyncService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Load config
config_path = os.environ.get("CONFIG_PATH", "config/default.json")
config = Config(config_path)

API_PREFIX = "/api/v1"
SERVICE_NAME = "jira-api"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — connect DB and initialize services on startup."""
    # Startup
    await connect_db(config)
    init_auth(config)

    jira_client = JiraClient(config)
    gcs_client = GCSClient(config)
    sync_service = JiraSyncService(config, jira_client, gcs_client)

    init_sync_routes(sync_service)
    init_issue_routes(jira_client, config)

    logger.info("%s started", SERVICE_NAME)
    yield

    # Shutdown
    await close_db()
    logger.info("%s stopped", SERVICE_NAME)


app = FastAPI(
    title="Jira Dashboard API",
    description="Standalone API service for Jira dashboard data, sync, and issue management.",
    version="1.0.0",
    root_path=config.get("server.root_path", ""),
    lifespan=lifespan,
)

# CORS
cors_origins = config.get("server.cors_origins", ["http://localhost:3000", "http://localhost:5173"])
if isinstance(cors_origins, str):
    cors_origins = [o.strip() for o in cors_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(dashboard_router, prefix=API_PREFIX)
app.include_router(sync_router, prefix=API_PREFIX)
app.include_router(issues_router, prefix=API_PREFIX)


@app.get("/health/live", tags=["health"])
async def health_live():
    """Liveness probe."""
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/health/ready", tags=["health"])
async def health_ready():
    """Readiness probe — checks DB connectivity."""
    try:
        from src.db import get_db
        db = get_db()
        await db.command("ping")
        return {"status": "ok", "service": SERVICE_NAME, "database": "connected"}
    except Exception as exc:
        return {"status": "degraded", "service": SERVICE_NAME, "database": str(exc)}
