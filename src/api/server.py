"""FastAPI server for position management and pipeline control.

Provides:
- REST API for CRUD operations on positions
- Manual pipeline trigger endpoint
- Scheduled daily pipeline runs via APScheduler
- Mobile-friendly web UI served at /
- Output browsing and digest viewing
- LDAP authentication with brute-force protection (optional)
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..auth.middleware import (
    AUTH_ENABLED,
    LoginRequest,
    attempt_login,
    clear_auth_cookie,
    create_auth_cookie,
    get_client_ip,
    require_auth,
)
from ..config_loader import resolve_config_path
from ..main import run_pipeline, setup_logging
from ..models import Position, PositionsFile
from ..positions_loader import load_positions, save_positions

load_dotenv()
setup_logging(os.getenv("LOG_LEVEL", "INFO"))

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def scheduled_pipeline_run():
    """Run the pipeline on schedule."""
    logger.info("Scheduled pipeline run starting...")
    try:
        use_ollama = os.getenv("USE_OLLAMA", "true").lower() == "true"
        output_base = os.getenv("OUTPUT_BASE_PATH", "output")
        result = run_pipeline(output_base=output_base, use_ollama=use_ollama)
        logger.info(f"Scheduled run complete: {result}")
    except Exception as e:
        logger.error(f"Scheduled run failed: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_hour = int(os.getenv("PIPELINE_RUN_HOUR", "6"))
    run_minute = int(os.getenv("PIPELINE_RUN_MINUTE", "30"))
    tz = os.getenv("PIPELINE_TIMEZONE", "America/New_York")

    scheduler.add_job(
        scheduled_pipeline_run,
        "cron",
        hour=run_hour,
        minute=run_minute,
        timezone=tz,
        id="daily_pipeline",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started: daily run at {run_hour:02d}:{run_minute:02d} {tz}")

    yield

    scheduler.shutdown()


app = FastAPI(
    title="Market Pipeline",
    description="Position-driven market intelligence pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

POSITIONS_PATH = os.getenv("POSITIONS_PATH", "")
if not POSITIONS_PATH:
    try:
        POSITIONS_PATH = str(resolve_config_path("positions.yaml"))
    except FileNotFoundError:
        POSITIONS_PATH = "config/positions.yaml"


# ── Request/Response Models ──────────────────────────────────────────


class PositionInput(BaseModel):
    ticker: str
    weight: float


class PositionUpdate(BaseModel):
    positions: list[PositionInput]


class PipelineRunRequest(BaseModel):
    date: Optional[str] = None
    use_ollama: bool = True


# ── Routes ───────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the web UI (no auth required — login is handled in the UI)."""
    web_path = Path("web/index.html")
    if web_path.exists():
        return HTMLResponse(web_path.read_text())
    return HTMLResponse("<h1>Market Pipeline API</h1><p>Web UI not found.</p>")


# ── Auth Routes (no auth required) ──────────────────────────────────


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """Check authentication status."""
    if not AUTH_ENABLED:
        return {"authenticated": True, "auth_enabled": False, "user": "local"}

    from ..auth.jwt_handler import verify_token
    from ..auth.middleware import COOKIE_NAME

    token = request.cookies.get(COOKIE_NAME)
    if token:
        claims = verify_token(token)
        if claims:
            return {
                "authenticated": True,
                "auth_enabled": True,
                "user": claims.get("sub", "unknown"),
            }
    return {"authenticated": False, "auth_enabled": True, "user": None}


@app.post("/api/auth/login")
async def login(request: Request, body: LoginRequest, response: Response):
    """Login with LDAP credentials."""
    if not AUTH_ENABLED:
        return {"status": "auth_disabled", "message": "Authentication is not enabled"}

    client_ip = get_client_ip(request)
    success, message = attempt_login(body.username, body.password, client_ip)

    if success:
        create_auth_cookie(response, body.username)
        return {"status": "authenticated", "user": body.username}
    else:
        raise HTTPException(status_code=401, detail=message)


@app.post("/api/auth/logout")
async def logout(response: Response):
    """Clear session."""
    clear_auth_cookie(response)
    return {"status": "logged_out"}


# ── Protected API Routes ────────────────────────────────────────────


@app.get("/api/positions")
async def get_positions(user: dict = Depends(require_auth)):
    """Get current positions."""
    try:
        pf = load_positions(POSITIONS_PATH)
        return {
            "positions": [
                {"ticker": p.ticker, "weight": p.weight} for p in pf.positions
            ],
            "weight_sum": round(pf.weight_sum(), 4),
            "warning": pf.weight_warning(),
        }
    except FileNotFoundError:
        return {"positions": [], "weight_sum": 0, "warning": "No positions file found"}


@app.put("/api/positions")
async def update_positions(update: PositionUpdate, user: dict = Depends(require_auth)):
    """Replace all positions."""
    try:
        positions = [
            Position(ticker=p.ticker, weight=p.weight) for p in update.positions
        ]
        pf = PositionsFile(positions=positions)
        save_positions(pf, POSITIONS_PATH)
        return {
            "status": "updated",
            "positions_count": len(positions),
            "weight_sum": round(pf.weight_sum(), 4),
            "warning": pf.weight_warning(),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/positions")
async def add_position(position: PositionInput, user: dict = Depends(require_auth)):
    """Add a single position."""
    try:
        pf = load_positions(POSITIONS_PATH)
    except FileNotFoundError:
        pf = PositionsFile(positions=[])

    for p in pf.positions:
        if p.ticker.upper() == position.ticker.upper():
            raise HTTPException(
                status_code=400, detail=f"Ticker {position.ticker} already exists"
            )

    pf.positions.append(Position(ticker=position.ticker, weight=position.weight))
    save_positions(pf, POSITIONS_PATH)

    return {"status": "added", "ticker": position.ticker.upper()}


@app.delete("/api/positions/{ticker}")
async def delete_position(ticker: str, user: dict = Depends(require_auth)):
    """Remove a position by ticker."""
    try:
        pf = load_positions(POSITIONS_PATH)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No positions file")

    ticker_upper = ticker.upper()
    original_count = len(pf.positions)
    pf.positions = [p for p in pf.positions if p.ticker != ticker_upper]

    if len(pf.positions) == original_count:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")

    save_positions(pf, POSITIONS_PATH)
    return {"status": "deleted", "ticker": ticker_upper}


@app.post("/api/pipeline/run")
async def trigger_pipeline(
    request: PipelineRunRequest = PipelineRunRequest(),
    user: dict = Depends(require_auth),
):
    """Manually trigger a pipeline run."""
    try:
        run_date = date.fromisoformat(request.date) if request.date else None
        output_base = os.getenv("OUTPUT_BASE_PATH", "output")
        result = run_pipeline(
            run_date=run_date, output_base=output_base, use_ollama=request.use_ollama
        )
        return {"status": "completed", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pipeline/status")
async def pipeline_status(user: dict = Depends(require_auth)):
    """Get pipeline and scheduler status."""
    jobs = scheduler.get_jobs()
    next_run = None
    if jobs:
        next_run = str(jobs[0].next_run_time)

    return {
        "scheduler_running": scheduler.running,
        "next_scheduled_run": next_run,
        "jobs_count": len(jobs),
    }


@app.get("/api/outputs")
async def list_outputs(user: dict = Depends(require_auth)):
    """List available output directories."""
    output_base = Path(os.getenv("OUTPUT_BASE_PATH", "output"))
    if not output_base.exists():
        return {"outputs": []}

    dirs = sorted(
        [
            d.name
            for d in output_base.iterdir()
            if d.is_dir() and d.name.endswith("-analysis")
        ],
        reverse=True,
    )

    return {"outputs": dirs}


@app.get("/api/outputs/{date_str}/digest")
async def get_digest(date_str: str, user: dict = Depends(require_auth)):
    """Get the digest for a specific date."""
    output_base = Path(os.getenv("OUTPUT_BASE_PATH", "output"))
    digest_path = (
        output_base / f"{date_str}-analysis" / f"market_digest-{date_str}.md"
    )

    if not digest_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Digest not found for {date_str}"
        )

    return {"date": date_str, "content": digest_path.read_text()}
