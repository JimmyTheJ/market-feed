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
from ..models import DEFAULT_CURRENCIES, Account, AccountsFile, Position, PositionsFile, TransactionRecord, TransactionsFile
from ..positions_loader import load_positions, save_positions
from ..portfolio_ledger import compute_pnl, derive_positions_from_transactions
from ..price_service import get_option_price, get_prices
from ..profile_manager import (
    create_profile,
    delete_profile,
    get_profile,
    list_profiles,
    update_profile_settings,
)
from ..accounts_manager import (
    delete_account,
    get_account_transactions_path,
    has_account_transactions,
    load_accounts,
    load_all_account_transactions,
    save_accounts,
)
from ..transactions_loader import load_all_profile_transactions, load_transactions, save_transactions

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
        ollama_temperature: float = 0.3
        ollama_max_tokens: int = 2048
        # Fall back to profile's settings.yaml summarization config
        if not ollama_model:
            try:
                prof_settings = load_yaml_config(
                    "settings.yaml", merge_with_defaults=True, profile=profile_id
                )
                summarization = prof_settings.get("summarization", {})
                ollama_model = summarization.get("ollama_model") or None
                ollama_temperature = float(summarization.get("temperature", 0.3))
                ollama_max_tokens = int(summarization.get("max_tokens", 2048))
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
                ollama_temperature=ollama_temperature,
                ollama_max_tokens=ollama_max_tokens,
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
    cost_basis_method: Optional[str] = None  # fifo | average_cost | specific_lot


class TransactionCreate(BaseModel):
    """Request body for creating or updating a transaction."""

    date: str  # ISO date string e.g. "2024-03-15"
    ticker: str
    action: str  # "buy" or "sell"
    quantity: float
    price: float
    currency: str = "USD"
    commission: float = 0.0
    position_type: str = "equity"
    option_type: Optional[str] = None
    option_direction: Optional[str] = None
    strike: Optional[float] = None
    expiration: Optional[str] = None
    lot_id: Optional[str] = None
    notes: str = ""


class PipelineRunRequest(BaseModel):
    date: Optional[str] = None
    use_ollama: bool = True
    run_label: str = ""
    pipeline_mode: str = "positions"  # "positions" or "general"


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
    """Get current positions with live prices and computed weights.

    When the profile has account-based transactions, positions are derived
    and aggregated from all accounts. Falls back to positions.yaml otherwise.
    """
    positions_path = _resolve_positions_path(profile)
    source = "manual"

    # Derive positions from account transactions when available
    if profile and has_account_transactions(profile):
        try:
            all_txs = load_all_account_transactions(profile)
            if all_txs:
                try:
                    existing_pf = load_positions(positions_path)
                    currencies = existing_pf.currencies
                except FileNotFoundError:
                    currencies = None
                pf = derive_positions_from_transactions(all_txs, currencies)
                source = "ledger"
        except Exception as e:
            logger.warning(f"Failed to derive positions from accounts: {e}")
            source = "manual"

    if source == "manual":
        try:
            pf = load_positions(positions_path)
        except FileNotFoundError:
            return {
                "positions": [],
                "total_value": 0,
                "display_currency": display_currency.upper(),
                "currencies": list(DEFAULT_CURRENCIES),
                "source": "manual",
            }

    # Smart refresh: re-fetch stale prices during market hours (skip cash)
    tickers_currencies = [
        (p.ticker, p.currency) for p in pf.positions if p.position_type != "cash"
    ]
    stale = get_tickers_needing_refresh(tickers_currencies, profile=profile)
    if stale:
        get_prices(stale)

    resp = _build_positions_response(pf, display_currency.upper())
    resp["source"] = source
    return resp


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
    """Update pipeline settings for a profile."""
    settings = {}
    if body.scheduler_enabled is not None:
        settings["scheduler_enabled"] = body.scheduler_enabled
    if body.use_ollama is not None:
        settings["use_ollama"] = body.use_ollama
    if body.ollama_model is not None:
        settings["ollama_model"] = body.ollama_model

    # cost_basis_method is stored in the profile's settings.yaml, not profiles.yaml
    if body.cost_basis_method is not None:
        valid = {"fifo", "average_cost", "specific_lot"}
        if body.cost_basis_method not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"cost_basis_method must be one of: {', '.join(sorted(valid))}",
            )
        _update_settings_yaml(profile_id, {"portfolio": {"cost_basis_method": body.cost_basis_method}})

    updated = update_profile_settings(profile_id, settings)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")
    return {"status": "updated", "profile": updated}


def _update_settings_yaml(profile_id: str, updates: dict) -> None:
    """Deep-merge *updates* into the profile's settings.yaml."""
    from pathlib import Path
    settings_path = Path(f"config/profiles/{profile_id}/settings.yaml")
    if settings_path.exists():
        with open(settings_path) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    def _deep_merge(base: dict, override: dict) -> dict:
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = _deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    data = _deep_merge(data, updates)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)



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
        ollama_temperature: float = 0.3
        ollama_max_tokens: int = 2048
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
                        summarization = prof_settings.get("summarization", {})
                        ollama_model = summarization.get("ollama_model") or None
                        ollama_temperature = float(summarization.get("temperature", 0.3))
                        ollama_max_tokens = int(summarization.get("max_tokens", 2048))
                    except Exception:
                        pass
        result = run_pipeline(
            run_date=run_date,
            output_base=output_base,
            use_ollama=request.use_ollama,
            ollama_model=ollama_model,
            ollama_temperature=ollama_temperature,
            ollama_max_tokens=ollama_max_tokens,
            profile=profile,
            run_label=request.run_label,
            pipeline_mode=request.pipeline_mode,
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


@app.delete("/api/outputs/{dir_name}")
async def delete_output(
    dir_name: str,
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Delete an output directory and all its contents."""
    import shutil

    output_base = Path(os.getenv("OUTPUT_BASE_PATH", "output"))
    if profile:
        output_base = output_base / profile
    output_dir = output_base / dir_name

    if not output_dir.exists():
        raise HTTPException(status_code=404, detail=f"Output not found: {dir_name}")

    # Safety check: only delete directories that look like pipeline outputs
    if "-analysis" not in dir_name:
        raise HTTPException(status_code=400, detail="Invalid output directory name")

    try:
        shutil.rmtree(output_dir)
        logger.info(f"Deleted output directory: {output_dir}")
        return {"status": "deleted", "dir_name": dir_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {e}")


# ── Account management endpoints ─────────────────────────────────────────────


class AccountCreate(BaseModel):
    name: str
    currency: str = "USD"
    description: str = ""


class AccountUpdate(BaseModel):
    name: str | None = None
    currency: str | None = None
    description: str | None = None
    order: int | None = None


class AccountsReorder(BaseModel):
    order: list[str]  # list of account IDs in desired order


@app.get("/api/accounts")
async def list_accounts_endpoint(
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """List all accounts for a profile, sorted by order."""
    if not profile:
        raise HTTPException(status_code=400, detail="profile parameter is required")
    accts = load_accounts(profile)
    return {"accounts": [a.model_dump() for a in accts.accounts]}


@app.post("/api/accounts")
async def create_account_endpoint(
    body: AccountCreate,
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Create a new account for a profile."""
    if not profile:
        raise HTTPException(status_code=400, detail="profile parameter is required")
    accts = load_accounts(profile)
    new_account = Account(
        name=body.name,
        currency=body.currency,
        description=body.description,
        order=len(accts.accounts),
    )
    accts.accounts.append(new_account)
    save_accounts(accts, profile)
    return {"status": "created", "account": new_account.model_dump()}


@app.put("/api/accounts/{account_id}")
async def update_account_endpoint(
    account_id: str,
    body: AccountUpdate,
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Update an account's name, currency, description, or order."""
    if not profile:
        raise HTTPException(status_code=400, detail="profile parameter is required")
    accts = load_accounts(profile)
    for acct in accts.accounts:
        if acct.id == account_id:
            if body.name is not None:
                acct.name = body.name
            if body.currency is not None:
                acct.currency = body.currency
            if body.description is not None:
                acct.description = body.description
            if body.order is not None:
                acct.order = body.order
            save_accounts(accts, profile)
            return {"status": "updated", "account": acct.model_dump()}
    raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")


@app.delete("/api/accounts/{account_id}")
async def delete_account_endpoint(
    account_id: str,
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Delete an account and all its transactions."""
    if not profile:
        raise HTTPException(status_code=400, detail="profile parameter is required")
    removed = delete_account(profile, account_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")
    return {"status": "deleted", "id": account_id}


@app.post("/api/accounts/reorder")
async def reorder_accounts_endpoint(
    body: AccountsReorder,
    profile: str | None = None,
    user: dict = Depends(require_auth),
):
    """Update the display order of accounts.

    Body: { "order": ["id1", "id2", "id3"] } — full list of IDs in desired order.
    """
    if not profile:
        raise HTTPException(status_code=400, detail="profile parameter is required")
    accts = load_accounts(profile)
    id_to_acct = {a.id: a for a in accts.accounts}
    ordered = []
    for i, acct_id in enumerate(body.order):
        if acct_id in id_to_acct:
            id_to_acct[acct_id].order = i
            ordered.append(id_to_acct[acct_id])
    # Append any accounts not in the order list (safety)
    seen = set(body.order)
    for a in accts.accounts:
        if a.id not in seen:
            a.order = len(ordered)
            ordered.append(a)
    accts.accounts = ordered
    save_accounts(accts, profile)
    return {"status": "reordered", "accounts": [a.model_dump() for a in accts.accounts]}


# ── Transaction ledger endpoints ─────────────────────────────────────────────


@app.get("/api/transactions")
async def list_transactions_endpoint(
    profile: str | None = None,
    account_id: str | None = None,
    user: dict = Depends(require_auth),
):
    """List transactions for a profile/account, sorted newest-first."""
    txs = load_transactions(profile=profile, account_id=account_id)
    sorted_txs = sorted(txs.transactions, key=lambda t: t.date, reverse=True)
    return {"transactions": [t.model_dump() for t in sorted_txs]}


@app.post("/api/transactions")
async def add_transaction_endpoint(
    body: TransactionCreate,
    profile: str | None = None,
    account_id: str | None = None,
    user: dict = Depends(require_auth),
):
    """Add a new transaction to the ledger (profile-root or account-scoped)."""
    from datetime import date as _date
    try:
        record = TransactionRecord(
            date=_date.fromisoformat(body.date),
            ticker=body.ticker,
            action=body.action,
            quantity=body.quantity,
            price=body.price,
            currency=body.currency,
            commission=body.commission,
            position_type=body.position_type,
            option_type=body.option_type,
            option_direction=body.option_direction,
            strike=body.strike,
            expiration=body.expiration,
            lot_id=body.lot_id,
            notes=body.notes,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    txs = load_transactions(profile=profile, account_id=account_id)
    txs.transactions.append(record)
    save_transactions(txs, profile=profile, account_id=account_id)
    return {"status": "created", "transaction": record.model_dump()}


@app.put("/api/transactions/{tx_id}")
async def update_transaction_endpoint(
    tx_id: str,
    body: TransactionCreate,
    profile: str | None = None,
    account_id: str | None = None,
    user: dict = Depends(require_auth),
):
    """Replace an existing transaction by ID."""
    from datetime import date as _date
    txs = load_transactions(profile=profile, account_id=account_id)
    for i, tx in enumerate(txs.transactions):
        if tx.id == tx_id:
            try:
                updated = TransactionRecord(
                    id=tx_id,
                    date=_date.fromisoformat(body.date),
                    ticker=body.ticker,
                    action=body.action,
                    quantity=body.quantity,
                    price=body.price,
                    currency=body.currency,
                    commission=body.commission,
                    position_type=body.position_type,
                    option_type=body.option_type,
                    option_direction=body.option_direction,
                    strike=body.strike,
                    expiration=body.expiration,
                    lot_id=body.lot_id,
                    notes=body.notes,
                )
            except Exception as e:
                raise HTTPException(status_code=422, detail=str(e))
            txs.transactions[i] = updated
            save_transactions(txs, profile=profile, account_id=account_id)
            return {"status": "updated", "transaction": updated.model_dump()}
    raise HTTPException(status_code=404, detail=f"Transaction '{tx_id}' not found")


@app.delete("/api/transactions/{tx_id}")
async def delete_transaction_endpoint(
    tx_id: str,
    profile: str | None = None,
    account_id: str | None = None,
    user: dict = Depends(require_auth),
):
    """Delete a transaction by ID."""
    txs = load_transactions(profile=profile, account_id=account_id)
    before = len(txs.transactions)
    txs.transactions = [t for t in txs.transactions if t.id != tx_id]
    if len(txs.transactions) == before:
        raise HTTPException(status_code=404, detail=f"Transaction '{tx_id}' not found")
    save_transactions(txs, profile=profile, account_id=account_id)
    return {"status": "deleted", "id": tx_id}


@app.get("/api/pnl")
async def get_pnl_endpoint(
    profile: str | None = None,
    account_id: str | None = None,
    display_currency: str = "USD",
    user: dict = Depends(require_auth),
):
    """Compute and return P&L.

    If account_id is given, computes P&L for that specific account.
    Otherwise, aggregates across all accounts (or legacy transactions.yaml).
    """
    if account_id:
        txs = load_transactions(profile=profile, account_id=account_id)
    else:
        txs = load_all_profile_transactions(profile=profile)

    if not txs.transactions:
        return {"pnl": None, "message": "No transactions recorded yet"}

    method = "fifo"
    try:
        prof_settings = load_yaml_config("settings.yaml", merge_with_defaults=True, profile=profile)
        method = prof_settings.get("portfolio", {}).get("cost_basis_method", "fifo")
    except Exception:
        pass

    tickers = list({t.ticker for t in txs.transactions if t.position_type != "cash"})
    prices = get_prices(tickers) if tickers else {}

    native_currencies = list({t.currency for t in txs.transactions})
    if display_currency not in native_currencies:
        native_currencies.append(display_currency)
    forex = get_rates_to(display_currency, native_currencies)

    pnl = compute_pnl(txs.transactions, prices, forex, method, display_currency)
    return {"pnl": pnl.model_dump(), "cost_basis_method": method}
