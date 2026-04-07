# Position-Driven Market Pipeline

A daily, low-stimulation market intelligence pipeline that reads your portfolio positions and automatically generates relevant, scored market digests.

## Quick Start

### Local (no Docker)

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your settings (Ollama URL, schedule, etc.)

# Run the pipeline once
python -m src.main

# Or start the API server (includes web UI + scheduler)
uvicorn src.api.server:app --host 0.0.0.0 --port 8000
```

### Docker (Recommended for Server)

```bash
# Create the shared network (one-time)
docker network create proxy

# Copy and configure environment
cp .env.example .env
# Edit .env with your settings

# Build and start
docker-compose up -d

# View logs
docker-compose logs -f pipeline

# Stop
docker-compose down
```

The web UI is accessible at `http://your-server:8000`

## Architecture

```
positions.yaml ──▶ Metadata Enrichment ──▶ Daily Snapshot
                                              │
RSS Feeds ──▶ Ingestion ──▶ Normalization ──▶ Scoring ──▶ Summarization ──▶ Digest
                                              │
                                     (Ollama LLM optional)
```

### Core Concept

**Your portfolio determines what information is relevant.** Instead of consuming everything and filtering mentally, the pipeline uses your positions as the filter.

## Project Structure

```
├── config/
│   ├── defaults/                  # Shipped defaults (git-tracked, never edit)
│   │   ├── positions.yaml
│   │   ├── sources.yaml
│   │   └── settings.yaml
│   ├── positions.yaml             # Your overrides (gitignored, optional)
│   ├── sources.yaml
│   └── settings.yaml
├── data/
│   ├── defaults/metadata/         # Shipped default metadata
│   │   └── ticker_metadata.yaml
│   └── metadata/                  # Your override (gitignored, optional)
│       └── ticker_metadata.yaml
├── output/                        # Daily dated output (auto-generated)
│   └── YYYY-MM-DD-analysis/
│       ├── daily-positions-YYYY-MM-DD.yaml
│       ├── market_digest-YYYY-MM-DD.md
│       ├── raw_articles-YYYY-MM-DD.json
│       ├── ranked_articles-YYYY-MM-DD.json
│       ├── summary_payload-YYYY-MM-DD.json
│       └── run_log-YYYY-MM-DD.txt
├── src/
│   ├── main.py                    # Pipeline orchestrator
│   ├── config_loader.py           # Config resolution (override → default)
│   ├── models.py                  # Data models (Pydantic)
│   ├── positions_loader.py        # Load/save positions
│   ├── metadata_lookup.py         # 3-tier ticker enrichment
│   ├── ingestion.py               # RSS feed fetching
│   ├── normalization.py           # Article normalization
│   ├── scoring.py                 # Relevance scoring engine
│   ├── summarizer.py              # Ollama-powered summaries
│   ├── digest_writer.py           # Markdown digest generation
│   ├── storage.py                 # File I/O for artifacts
│   ├── api/
│   │   └── server.py              # FastAPI server + scheduler + auth
│   └── auth/
│       ├── ldap_auth.py           # LDAP authentication client
│       ├── jwt_handler.py         # JWT token management
│       ├── rate_limiter.py        # Brute-force protection
│       └── middleware.py          # Auth dependency + login/logout
├── web/
│   └── index.html                 # Mobile-friendly web UI with login
├── tests/                         # pytest test suite (118 tests)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Configuration

### Defaults & Overrides

Config files use a fallback pattern:
1. **User override** (`config/positions.yaml`) — takes precedence, gitignored
2. **Shipped default** (`config/defaults/positions.yaml`) — git-tracked, never edit

To customize: copy a default to the parent directory and edit it:
```bash
cp config/defaults/positions.yaml config/positions.yaml
# Edit config/positions.yaml with your positions
```

For `settings.yaml`, partial overrides are **deep-merged** with defaults — you only need to specify the values you want to change.

### Future Profiles

The config loader supports a profiles directory for future use:
```
config/profiles/aggressive/positions.yaml
config/profiles/conservative/positions.yaml
```

## Editing Positions

### Via YAML (direct)
Edit `config/positions.yaml`:
```yaml
positions:
  - ticker: IBIT
    weight: 0.15
  - ticker: QQQ
    weight: 0.12
```

### Via Web UI (mobile-friendly)
Navigate to `http://your-server:8000` from any device. The web UI supports:
- Adding/removing positions
- Viewing portfolio weight distribution
- Triggering pipeline runs manually
- Browsing past digests

### Via API
```bash
# Get positions
curl http://localhost:8000/api/positions

# Add a position
curl -X POST http://localhost:8000/api/positions \
  -H "Content-Type: application/json" \
  -d '{"ticker": "NVDA", "weight": 0.10}'

# Delete a position
curl -X DELETE http://localhost:8000/api/positions/NVDA

# Replace all positions
curl -X PUT http://localhost:8000/api/positions \
  -H "Content-Type: application/json" \
  -d '{"positions": [{"ticker": "SPY", "weight": 0.5}, {"ticker": "QQQ", "weight": 0.5}]}'

# Trigger a pipeline run
curl -X POST http://localhost:8000/api/pipeline/run

# Run without AI summaries
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"use_ollama": false}'
```

## Authentication (LDAP)

Authentication is **optional** and disabled by default (`AUTH_ENABLED=false`). Enable it for server deployments behind Nginx Proxy Manager.

### Setup

In `.env`:
```bash
AUTH_ENABLED=true
LDAP_SERVER=ldap://your-ldap-server:389
LDAP_BASE_DN=dc=example,dc=com
LDAP_USER_DN_TEMPLATE=uid={username},ou=users,dc=example,dc=com
LDAP_AUTH_METHOD=direct    # or "search" for search-then-bind
JWT_SECRET=your-random-secret-here
COOKIE_SECURE=true         # set true when behind HTTPS
```

### Features
- **LDAP integration** — works with any LDAP server (OpenLDAP, Active Directory)
- **Direct bind** or **search-then-bind** authentication patterns
- **Group membership** checks (groupOfNames, groupOfUniqueNames, posixGroup)
- **JWT httpOnly cookies** — prevents XSS token theft
- **Dual rate limiting**: per-IP (5 attempts/15min) + per-username (10 attempts/30min)
- **Fail2ban-compatible** log format for server-side intrusion detection
- **Generic error messages** — never reveals whether a username exists

## Docker Network

The compose file uses an **external network** pattern, ideal when running multiple Docker projects behind Nginx Proxy Manager:

```yaml
networks:
  default:
    name: ${DOCKER_NETWORK:-proxy}
    external: true
```

Create the network once: `docker network create proxy`

All your Docker projects can join the same network for inter-container communication through NPM.

## Ollama Integration

The summarizer uses a local Ollama instance for AI-powered summaries. Set these in `.env`:

```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
USE_OLLAMA=true
```

When running in Docker, the container connects to the host's Ollama via `host.docker.internal:11434` (configured automatically in docker-compose.yml).

If Ollama is unavailable, the system falls back to extractive summaries (no AI).

## Scheduling

The API server includes APScheduler for automated daily runs. Configure in `.env`:

```
PIPELINE_RUN_HOUR=6
PIPELINE_RUN_MINUTE=30
PIPELINE_TIMEZONE=America/New_York
```

For standalone cron (without the API server):
```bash
30 6 * * * cd /path/to/market-pipeline && /path/to/python -m src.main >> logs/cron.log 2>&1
```

## News Sources

Pre-configured RSS feeds cover:
- **Crypto**: CoinDesk, CoinTelegraph, Decrypt, The Block
- **Markets/Macro**: MarketWatch, CNBC, Yahoo Finance
- **Energy**: OilPrice.com, EIA
- **Tesla/EV**: Electrek
- **Technology**: TechCrunch
- **International**: BBC Business, CNBC World

Edit `config/sources.yaml` to add/remove/disable feeds.

## Scoring System

Articles are scored against each position using:
1. Direct ticker mention (highest weight)
2. Keyword overlap
3. Theme overlap
4. Macro sensitivity overlap
5. Related term overlap
6. Source priority
7. Recency bonus
8. Portfolio weight multiplier

This ensures higher-weighted positions surface more relevant content.

## Testing

```bash
# Run all tests (118 tests)
pytest

# Run with coverage
pytest --cov=src --cov-report=term-missing

# Run specific test file
pytest tests/test_scoring.py -v
```

## License

MIT
