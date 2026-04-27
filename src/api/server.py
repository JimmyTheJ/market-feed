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

import httpx
import yaml

from ..auth.middleware import (
    AUTH_ENABLED,
    LoginRequest,
    attempt_login,
    clear_auth_cookie,
    create_auth_cookie,
    get_client_ip,
    require_auth,
)
from ..config_loader import load_yaml_config, resolve_config_path
from ..forex_service import get_rates_to
from ..main import run_pipeline, setup_logging
from ..market_hours import get_tickers_needing_refresh
from ..models import DEFAULT_CURRENCIES, Position, PositionsFile
from ..positions_loader import load_positions, save_positions
from ..price_service import get_option_price, get_prices
from ..profile_manager import (
    create_profile,
    delete_profile,
    get_profile,
    list_profiles,
    update_profile_settings,
)

load_dotenv()
setup_logging(os.getenv("LOG_LEVEL", "INFO"))

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def scheduled_pipeline_run(run_label: str = ""):
    """Run the pipeline on schedule for all profiles with scheduling enabled."""
    label_display = f" ({run_label})" if run_label else ""
    profiles = list_profiles()
    enabled = [p for p in profiles if p.get("scheduler_enabled", True)]

    if not enabled:
        logger.info(f"Scheduled run{label_display}: no profiles with scheduling enabled")
        return

    for profile in enabled:
        profile_id = profile["id"]
        use_ollama = profile.get("use_ollama", True)
        ollama_model = profile.get("ollama_model") or None
        # Fall back to profile's settings.yaml summarization config
        if not ollama_model:
            try:
                prof_settings = load_yaml_config(
                    "settings.yaml", merge_with_defaults=True, profile=profile_id
                )
                ollama_model = (
                    prof_settings.get("summarization", {}).get("ollama_model") or None
                )
            except Exception:
                pass
        output_base = os.getenv("OUTPUT_BASE_PATH", "output")
        logger.info(
            f"Scheduled pipeline run{label_display} for profile '{profile_id}'..."
        )
        try:
            result = run_pipeline(
                output_base=output_base,
                use_ollama=use_ollama,
                ollama_model=ollama_model,
                run_label=run_label,
                profile=profile_id,
            )
            logger.info(
                f"Scheduled run{label_display} for '{profile_id}' complete: {result}"
            )
        except Exception as e:
            logger.error(
                f"Scheduled run{label_display} for '{profile_id}' failed: {e}",
                exc_info=True,
            )


def _load_schedule_config() -> tuple[list[dict], str]:
    """Load pipeline schedule from settings.yaml or env vars."""
    settings_path = Path("config/defaults/settings.yaml")
    if settings_path.exists():
        with open(settings_path) as f:
            settings = yaml.safe_load(f) or {}
        schedule_cfg = settings.get("schedule", {})
        runs = schedule_cfg.get("runs", [])
        tz = schedule_cfg.get("timezone", "America/New_York")
        if runs:
            return runs, tz

    # Fallback to env vars (single run, backward compatible)
    run_hour = int(os.getenv("PIPELINE_RUN_HOUR", "6"))
    run_minute = int(os.getenv("PIPELINE_RUN_MINUTE", "30"))
    tz = os.getenv("PIPELINE_TIMEZONE", "America/New_York")
    return [{"label": "", "hour": run_hour, "minute": run_minute}], tz


@asynccontextmanager
async def lifespan(app: FastAPI):
    runs, tz = _load_schedule_config()

    for run_cfg in runs:
        label = run_cfg.get("label", "")
        hour = run_cfg.get("hour", 6)
        minute = run_cfg.get("minute", 0)
        job_id = f"pipeline_{label.lower()}" if label else "daily_pipeline"

        scheduler.add_job(
            scheduled_pipeline_run,
            "cron",
            hour=hour,
            minute=minute,
            timezone=tz,
            id=job_id,
            replace_existing=True,
            kwargs={"run_label": label},
        )
        label_display = f" ({label})" if label else ""
        logger.info(
            f"Scheduler: pipeline{label_display} at {hour:02d}:{minute:02d} {tz}"
        )

    scheduler.start()
    logger.info(f"Scheduler started with {len(runs)} job(s)")

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


class ProfileSettingsUpdate(BaseModel):
    scheduler_enabled: Optional[bool] = None
    use_ollama: Optional[bool] = None
    ollama_model: Optional[str] = None


class PipelineRunRequest(BaseModel):
    date: Optional[str] = None
    use_ollama: bool = True
    run_label: str = ""


# ── Helpers ──────────────────────────────────────────────────────────


def _build_positions_response(
    pf: PositionsFile, display_currency: str = "USD"
) -> dict:
    """Build the full positions response with live prices and forex conversion."""
    tickers = [p.ticker for p in pf.positions if p.position_type != "cash"]
    prices = get_prices(tickers) if tickers else {}

    native_currencies = list({p.currency for p in pf.positions})
    if display_currency not in native_currencies:
        native_currencies.append(display_currency)
    forex = get_rates_to(display_currency, native_currencies)

    enriched = []
    total_value = 0.0
    for p in pf.positions:
        if p.position_type == "cash":
            effective_price = 1.0
        elif p.price_override is not None:
            effective_price = p.price_override
        elif p.position_type == "option" and p.option_type and p.strike and p.expiration:
            effective_price = get_option_price(
                p.ticker, p.expiration, p.option_type, p.strike
            )
        else:
            effective_price = prices.get(p.ticker)
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

        # Smart refresh: re-fetch stale prices during market hours (skip cash)
        tickers_currencies = [
            (p.ticker, p.currency) for p in pf.positions if p.position_type != "cash"
        ]
        stale = get_tickers_needing_refresh(tickers_currencies, profile=profile)
        if stale:
            get_prices(stale)  # fetches & updates cache

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


@app.patch("/api/profiles/{profile_id}/settings")
async def update_profile_pipeline_settings(
    profile_id: str,
    body: ProfileSettingsUpdate,
    user: dict = Depends(require_auth),
):
    """Update pipeline settings (scheduler_enabled, use_ollama, ollama_model) for a profile."""
    settings = {}
    if body.scheduler_enabled is not None:
        settings["scheduler_enabled"] = body.scheduler_enabled
    if body.use_ollama is not None:
        settings["use_ollama"] = body.use_ollama
    if body.ollama_model is not None:
        settings["ollama_model"] = body.ollama_model
    updated = update_profile_settings(profile_id, settings)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")
    return {"status": "updated", "profile": updated}


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
        # Pick up the profile's configured model if no override in the request
        ollama_model: str | None = None
        if profile:
            profile_data = get_profile(profile)
            if profile_data:
                ollama_model = profile_data.get("ollama_model") or None
                # Fall back to profile's settings.yaml summarization config
                if not ollama_model:
                    try:
                        prof_settings = load_yaml_config(
                            "settings.yaml", merge_with_defaults=True, profile=profile
                        )
                        ollama_model = (
                            prof_settings.get("summarization", {}).get("ollama_model")
                            or None
                        )
                    except Exception:
                        pass
        result = run_pipeline(
            run_date=run_date,
            output_base=output_base,
            use_ollama=request.use_ollama,
            ollama_model=ollama_model,
            profile=profile,
            run_label=request.run_label,
        )
        return {"status": "completed", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pipeline/status")
async def pipeline_status(user: dict = Depends(require_auth)):
    """Get pipeline and scheduler status."""
    jobs = scheduler.get_jobs()
    job_list = []
    for job in jobs:
        job_list.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })

    return {
        "scheduler_running": scheduler.running,
        "jobs": job_list,
        "jobs_count": len(jobs),
    }


@app.get("/api/ollama/models")
async def list_ollama_models(user: dict = Depends(require_auth)):
    """Fetch available models from the local Ollama instance."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base_url}/api/tags")
        response.raise_for_status()
        data = response.json()
        models = [m["name"] for m in data.get("models", [])]
        return {"models": models, "base_url": base_url}
    except Exception as e:
        logger.warning(f"Could not reach Ollama at {base_url}: {e}")
        return {"models": [], "base_url": base_url, "error": str(e)}


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
            if d.is_dir() and "-analysis" in d.name
        ],
        reverse=True,
    )

    return {"outputs": dirs}


@app.get("/api/outputs/{dir_name}/digest")
async def get_digest(
    dir_name: str,
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Get the digest for a specific output directory."""
    output_base = Path(os.getenv("OUTPUT_BASE_PATH", "output"))
    if profile:
        output_base = output_base / profile
    output_dir = output_base / dir_name

    if not output_dir.exists():
        raise HTTPException(status_code=404, detail=f"Output not found: {dir_name}")

    # Find any digest file in the directory
    digest_files = list(output_dir.glob("market_digest-*.md"))
    if not digest_files:
        raise HTTPException(
            status_code=404, detail=f"Digest not found in {dir_name}"
        )

    digest_path = digest_files[0]
    return {"date": dir_name, "content": digest_path.read_text()}
