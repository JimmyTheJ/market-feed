"""FastAPI server for position management and pipeline control.

Provides:
- REST API for CRUD operations on positions (shares-based)
- Live price lookups and forex conversion
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
from fastapi.responses import HTMLResponse
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
from ..forex_service import get_rates_to
from ..main import run_pipeline, setup_logging
from ..models import DEFAULT_CURRENCIES, Position, PositionsFile
from ..positions_loader import load_positions, save_positions
from ..price_service import get_prices
from ..profile_manager import (
    create_profile,
    delete_profile,
    get_profile,
    list_profiles,
)

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


def _resolve_positions_path(profile: str | None = None) -> str:
    """Resolve positions path, preferring profile-scoped path when provided."""
    if profile:
        try:
            return str(resolve_config_path("positions.yaml", profile=profile))
        except FileNotFoundError:
            pass
    return POSITIONS_PATH


# ── Request/Response Models ──────────────────────────────────────────


class PositionInput(BaseModel):
    ticker: str
    shares: float
    currency: str = "USD"
    price_override: Optional[float] = None
    position_type: str = "equity"
    option_type: Optional[str] = None
    option_direction: Optional[str] = None
    strike: Optional[float] = None
    expiration: Optional[str] = None


class PositionUpdate(BaseModel):
    positions: list[PositionInput]


class ProfileCreate(BaseModel):
    name: str
    username: str = ""
    default_currency: str = "USD"


class PipelineRunRequest(BaseModel):
    date: Optional[str] = None
    use_ollama: bool = True


# ── Helpers ──────────────────────────────────────────────────────────


def _build_positions_response(
    pf: PositionsFile, display_currency: str = "USD"
) -> dict:
    """Build the full positions response with live prices and forex conversion."""
    tickers = [p.ticker for p in pf.positions]
    prices = get_prices(tickers) if tickers else {}

    native_currencies = list({p.currency for p in pf.positions})
    if display_currency not in native_currencies:
        native_currencies.append(display_currency)
    forex = get_rates_to(display_currency, native_currencies)

    enriched = []
    total_value = 0.0
    for p in pf.positions:
        live_price = prices.get(p.ticker)
        # Use override price when set, otherwise fall back to live price
        effective_price = p.price_override if p.price_override is not None else live_price
        fx_rate = forex.get(p.currency, 1.0)
        # Each option contract represents 100 shares of the underlying
        multiplier = 100 if p.position_type == "option" else 1
        native_value = (p.shares * effective_price * multiplier) if effective_price is not None else None
        display_value = (native_value * fx_rate) if native_value is not None else None
        if display_value is not None:
            total_value += abs(display_value)

        item: dict = {
            "ticker": p.ticker,
            "shares": p.shares,
            "currency": p.currency,
            "price": round(effective_price, 4) if effective_price is not None else None,
            "price_override": p.price_override,
            "native_value": round(native_value, 2) if native_value is not None else None,
            "display_value": round(display_value, 2) if display_value is not None else None,
            "position_type": p.position_type,
        }
        if p.position_type == "option":
            item["option_type"] = p.option_type
            item["option_direction"] = p.option_direction
            item["strike"] = p.strike
            item["expiration"] = p.expiration
        enriched.append(item)

    for item in enriched:
        if total_value > 0 and item["display_value"] is not None:
            item["weight"] = round(abs(item["display_value"]) / total_value, 6)
        else:
            item["weight"] = None

    return {
        "positions": enriched,
        "total_value": round(total_value, 2),
        "display_currency": display_currency,
        "currencies": pf.currencies,
    }


# ── Routes ───────────────────────────────────────────────────────────


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the web UI (no auth required — login is handled in the UI)."""
    web_path = Path("web/index.html")
    if web_path.exists():
        return HTMLResponse(web_path.read_text(encoding="utf-8"), headers=_NO_CACHE_HEADERS)
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
async def get_positions(
    display_currency: str = "USD",
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Get current positions with live prices and computed weights."""
    positions_path = _resolve_positions_path(profile)
    try:
        pf = load_positions(positions_path)
        return _build_positions_response(pf, display_currency.upper())
    except FileNotFoundError:
        return {
            "positions": [],
            "total_value": 0,
            "display_currency": display_currency.upper(),
            "currencies": list(DEFAULT_CURRENCIES),
        }


@app.put("/api/positions")
async def update_positions(
    update: PositionUpdate,
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Replace all positions."""
    positions_path = _resolve_positions_path(profile)
    try:
        positions = [
            Position(
                ticker=p.ticker,
                shares=p.shares,
                currency=p.currency,
                price_override=p.price_override,
                position_type=p.position_type,
                option_type=p.option_type,
                option_direction=p.option_direction,
                strike=p.strike,
                expiration=p.expiration,
            )
            for p in update.positions
        ]
        try:
            existing = load_positions(positions_path)
            currencies = existing.currencies
        except FileNotFoundError:
            currencies = list(DEFAULT_CURRENCIES)

        pf = PositionsFile(positions=positions, currencies=currencies)
        save_positions(pf, positions_path)
        return {"status": "updated", "positions_count": len(positions)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/positions")
async def add_position(
    position: PositionInput,
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Add a single position."""
    positions_path = _resolve_positions_path(profile)
    try:
        pf = load_positions(positions_path)
    except FileNotFoundError:
        pf = PositionsFile(positions=[])

    for p in pf.positions:
        if p.position_type == "equity" and position.position_type == "equity":
            if p.ticker.upper() == position.ticker.upper():
                raise HTTPException(
                    status_code=400, detail=f"Ticker {position.ticker} already exists"
                )

    pf.positions.append(
        Position(
            ticker=position.ticker,
            shares=position.shares,
            currency=position.currency,
            price_override=position.price_override,
            position_type=position.position_type,
            option_type=position.option_type,
            option_direction=position.option_direction,
            strike=position.strike,
            expiration=position.expiration,
        )
    )
    save_positions(pf, positions_path)

    return {"status": "added", "ticker": position.ticker.upper()}


@app.delete("/api/positions/{ticker}")
async def delete_position(
    ticker: str,
    profile: str | None = None,
    index: int | None = None,
    user: dict = Depends(require_auth),
):
    """Remove a position by ticker (and optional index for options)."""
    positions_path = _resolve_positions_path(profile)
    try:
        pf = load_positions(positions_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No positions file")

    ticker_upper = ticker.upper()

    if index is not None and 0 <= index < len(pf.positions):
        # Delete by index (for options with same ticker)
        if pf.positions[index].ticker == ticker_upper:
            pf.positions.pop(index)
            save_positions(pf, positions_path)
            return {"status": "deleted", "ticker": ticker_upper}

    # Fall back to first match by ticker
    found_idx = None
    for i, p in enumerate(pf.positions):
        if p.ticker == ticker_upper:
            found_idx = i
            break

    if found_idx is None:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")

    pf.positions.pop(found_idx)
    save_positions(pf, positions_path)
    return {"status": "deleted", "ticker": ticker_upper}


@app.get("/api/currencies")
async def get_currencies(
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Get the configured list of currencies."""
    positions_path = _resolve_positions_path(profile)
    try:
        pf = load_positions(positions_path)
        return {"currencies": pf.currencies}
    except FileNotFoundError:
        return {"currencies": list(DEFAULT_CURRENCIES)}


# ── Ticker Search ───────────────────────────────────────────────────

YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"


@app.get("/api/ticker-search")
async def ticker_search(
    q: str = "",
    user: dict = Depends(require_auth),
):
    """Search for tickers via Yahoo Finance autosuggest."""
    query = q.strip()
    if len(query) < 1:
        return {"results": []}
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                YAHOO_SEARCH_URL,
                params={"q": query, "quotesCount": 10, "newsCount": 0},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5.0,
            )
            resp.raise_for_status()
            quotes = resp.json().get("quotes", [])
            results = [
                {
                    "symbol": q_item.get("symbol", ""),
                    "name": q_item.get("shortname") or q_item.get("longname", ""),
                    "exchange": q_item.get("exchDisp", q_item.get("exchange", "")),
                    "type": q_item.get("typeDisp", ""),
                }
                for q_item in quotes
                if q_item.get("isYahooFinance", False)
            ]
            return {"results": results}
    except Exception as e:
        logger.warning("Ticker search failed: %s", e)
        return {"results": []}


# ── Profile Routes ──────────────────────────────────────────────────


@app.get("/api/profiles")
async def get_profiles(user: dict = Depends(require_auth)):
    """List all profiles."""
    return {"profiles": list_profiles()}


@app.post("/api/profiles")
async def create_new_profile(body: ProfileCreate, user: dict = Depends(require_auth)):
    """Create a new profile with default config files."""
    try:
        profile = create_profile(
            name=body.name,
            username=body.username,
            default_currency=body.default_currency,
        )
        return {"status": "created", "profile": profile}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/profiles/{profile_id}")
async def get_single_profile(profile_id: str, user: dict = Depends(require_auth)):
    """Get a single profile by ID."""
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")
    return {"profile": profile}


@app.delete("/api/profiles/{profile_id}")
async def remove_profile(profile_id: str, user: dict = Depends(require_auth)):
    """Delete a profile and its config files."""
    if not delete_profile(profile_id):
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")
    return {"status": "deleted", "profile_id": profile_id}


@app.post("/api/pipeline/run")
async def trigger_pipeline(
    request: PipelineRunRequest = PipelineRunRequest(),
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Manually trigger a pipeline run."""
    try:
        run_date = date.fromisoformat(request.date) if request.date else None
        output_base = os.getenv("OUTPUT_BASE_PATH", "output")
        result = run_pipeline(
            run_date=run_date,
            output_base=output_base,
            use_ollama=request.use_ollama,
            profile=profile,
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
async def list_outputs(
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """List available output directories."""
    output_base = Path(os.getenv("OUTPUT_BASE_PATH", "output"))
    if profile:
        output_base = output_base / profile
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
async def get_digest(
    date_str: str,
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Get the digest for a specific date."""
    output_base = Path(os.getenv("OUTPUT_BASE_PATH", "output"))
    if profile:
        output_base = output_base / profile
    digest_path = (
        output_base / f"{date_str}-analysis" / f"market_digest-{date_str}.md"
    )

    if not digest_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Digest not found for {date_str}"
        )

    return {"date": date_str, "content": digest_path.read_text()}
