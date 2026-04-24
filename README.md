# HappyRobot FDE Challenge — Inbound Carrier Sales

AI-powered inbound carrier sales automation built on the HappyRobot platform.

## Architecture

```
Carrier calls in
  → HappyRobot voice agent (web call trigger)
    → During call: agent calls this API as webhook tools
      - GET  /api/v1/carriers/verify/{mc}  → FMCSA check
      - GET  /api/v1/loads/search           → find matching loads
    → After call: HappyRobot workflow POSTs to this API
      - POST /api/v1/calls                  → log outcome + data
        → Writes to PostgreSQL
          → Dashboard reads from same DB
```

## Quick Start

### Prerequisites
- Docker & Docker Compose
- FMCSA API key (register at https://mobile.fmcsa.dot.gov/QCDevsite/)

### Setup

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env → set FMCSA_API_KEY and API_KEY

# 2. Run
docker compose up --build

# 3. Verify
curl http://localhost:8000/api/v1/health -H "X-API-Key: hr-dev-key-2025"
```

API docs at: http://localhost:8000/docs

## API Endpoints

| Method | Path | Purpose | Consumer |
|--------|------|---------|----------|
| GET | `/api/v1/health` | Health check | Ops |
| GET | `/api/v1/carriers/verify/{mc}` | FMCSA verification | Agent |
| GET | `/api/v1/carriers/verify-dot/{dot}` | Verify by DOT# | Agent |
| GET | `/api/v1/carriers/search-name?name=` | Search by name | Agent |
| GET | `/api/v1/loads/search` | Search loads | Agent |
| GET | `/api/v1/loads/{load_id}` | Get load details | Agent |
| POST | `/api/v1/calls` | Log call record | Agent workflow |
| GET | `/api/v1/calls` | List calls | Dashboard |
| GET | `/api/v1/calls/stats` | Aggregated metrics | Dashboard |

## Authentication

All endpoints require `X-API-Key` header (except `/health` and docs).

## Deployment (Railway)

```bash
# Railway CLI
railway login
railway init
railway add --database postgres
railway variables set API_KEY=your-key FMCSA_API_KEY=your-fmcsa-key
railway up
```

## Tech Stack

- **FastAPI** + async SQLAlchemy + PostgreSQL
- **FMCSA QCMobile API** for carrier verification
- **Docker Compose** for local development
- **Railway** for production deployment
