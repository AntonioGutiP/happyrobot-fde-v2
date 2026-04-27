# Inbound Carrier Sales — AI Agent for Acme Logistics

An AI-powered system that handles inbound carrier calls for a freight brokerage. Carriers call in, the AI verifies them, finds matching loads, negotiates pricing, and books the deal — all without a human rep. Every call feeds a business dashboard that shows what's working and what needs attention.

Built on the [HappyRobot](https://happyrobot.ai) platform for the FDE Technical Challenge.

---

## Live Deployment

Everything is live and running:

| What | URL |
|------|-----|
| **Dashboard** | [happyrobot-fde-v2-production.up.railway.app/dashboard](https://happyrobot-fde-v2-production.up.railway.app/dashboard) |
| **API Docs** | [happyrobot-fde-v2-production.up.railway.app/docs](https://happyrobot-fde-v2-production.up.railway.app/docs) |
| **Health Check** | [happyrobot-fde-v2-production.up.railway.app/api/v1/health](https://happyrobot-fde-v2-production.up.railway.app/api/v1/health) |

API key for all endpoints: `hr-prod-2025-secure` (pass as `X-API-Key` header).

To test the voice agent, use the web call trigger in the HappyRobot platform (no phone number needed).

---

## What It Does

A carrier calls in. Here's what happens:

1. **Greeting** — AI introduces itself as Alex from carrier sales
2. **MC Verification** — Carrier gives their MC number, AI checks it against the FMCSA API in real-time
3. **Needs Gathering** — Equipment type, origin, destination
4. **Load Search** — AI searches the database for matching loads
5. **Pitch** — AI presents the load: lane, miles, rate, rate-per-mile
6. **Negotiation** — Up to 3 rounds. AI uses a deterministic pricing engine with configurable ceilings
7. **Booking** — If a price is agreed, AI mocks a transfer to dispatch and logs the deal
8. **Post-Call** — Outcome classification, sentiment analysis, booking confirmation email via Gmail

Every call — booked, rejected, declined, or no match — gets logged to the database. The dashboard reads from the same database, so it's always in sync.

---

## How to Run It Locally

### What you need

- Docker and Docker Compose
- An FMCSA API key (free — register at [mobile.fmcsa.dot.gov/QCDevsite](https://mobile.fmcsa.dot.gov/QCDevsite/))

### Steps

```bash
# 1. Clone the repo
git clone https://github.com/AntonioGutiP/happyrobot-fde-v2.git
cd happyrobot-fde-v2

# 2. Create your environment file
cp .env.example .env
```

Open `.env` and set your FMCSA key:
```
FMCSA_API_KEY=your_key_here
```

The rest of the defaults work out of the box (Postgres credentials, API key, ports).

```bash
# 3. Start everything
docker compose up --build
```

That's it. Two containers start up — Postgres and the API. The database tables and seed data (10 loads) are created automatically on first run.

```bash
# 4. Verify it's working
curl http://localhost:8000/api/v1/health -H "X-API-Key: hr-dev-key-2025"
```

You should get `{"status": "healthy"}`.

- **Dashboard**: [localhost:8000/dashboard](http://localhost:8000/dashboard)
- **API Docs**: [localhost:8000/docs](http://localhost:8000/docs)

### Stopping

```bash
docker compose down        # stop containers
docker compose down -v     # stop + wipe database
```

---

## How to Deploy to Railway

The repo is set up for Railway out of the box.

```bash
# 1. Install Railway CLI and log in
npm install -g @railway/cli
railway login

# 2. Create a new project
railway init

# 3. Add a Postgres database
railway add --database postgres

# 4. Set environment variables
railway variables set API_KEY=your-api-key
railway variables set FMCSA_API_KEY=your-fmcsa-key
railway variables set CORS_ORIGINS=*

# 5. Deploy
railway up
```

Railway auto-detects the Dockerfile and Procfile. HTTPS is handled automatically.

---

## Architecture

```
Carrier calls in
  → HappyRobot voice agent (web call trigger)
    → During call: agent uses these API tools via webhooks
        GET  /carriers/verify/{mc}     → real-time FMCSA check
        GET  /carriers/history/{mc}    → repeat caller detection
        GET  /loads/search             → find matching loads
        GET  /loads/market-context     → pricing strategy
        POST /negotiate                → deterministic counter-offers
        POST /carrier-preferences      → save unmet demand
        POST /calls                    → log call outcome
    → After call: post-call workflow fires
        POST /notifications/process-latest → route to email or demand alert
          → If booked: Gmail sends booking confirmation
```

Everything writes to one PostgreSQL database. The dashboard reads from the same database. One system.

### Project Structure

```
happyrobot-fde-v2/
├── api/
│   ├── main.py              # FastAPI app, route registration
│   ├── models.py             # SQLAlchemy models (loads, calls, bookings, preferences)
│   ├── schemas.py            # Pydantic request/response schemas
│   ├── database.py           # Async DB connection
│   ├── middleware.py         # API key authentication
│   ├── config.py             # Environment config
│   ├── seed_data.py          # 10 realistic loads, auto-seeds on startup
│   ├── routes/
│   │   ├── calls.py          # Log + list calls, auto-enrichment
│   │   ├── carriers.py       # FMCSA verification, history, qualification tiers
│   │   ├── loads.py          # Search, market context, reset
│   │   ├── negotiate.py      # Deterministic negotiation engine
│   │   ├── dashboard.py      # All dashboard metrics in one endpoint
│   │   ├── bookings.py       # Booking confirmations
│   │   ├── notifications.py  # Post-call routing (email, demand alerts)
│   │   ├── preferences.py    # Carrier preferences / unmet demand
│   │   └── health.py         # Health check
│   ├── services/
│   │   └── fmcsa.py          # FMCSA QCMobile API client
│   ├── static/
│   │   └── dashboard.html    # Business intelligence dashboard
│   ├── Dockerfile
│   └── requirements.txt
├── docker-compose.yml        # Local dev (API + Postgres)
├── Procfile                  # Railway deployment
├── .env.example              # Environment template
└── README.md
```

---

## API Reference

All endpoints require the `X-API-Key` header (except `/health` and `/docs`).

### Carrier Verification
| Method | Endpoint | What it does |
|--------|----------|--------------|
| GET | `/api/v1/carriers/verify/{mc}` | Checks MC number against FMCSA. Returns eligibility, DOT, legal name, authority status |
| GET | `/api/v1/carriers/history/{mc}` | Returns call history, booking count, qualification tier, preferred lanes |

### Load Management
| Method | Endpoint | What it does |
|--------|----------|--------------|
| GET | `/api/v1/loads/search` | Search by origin, destination, equipment type. Returns matching loads with rate-per-mile |
| GET | `/api/v1/loads/market-context/{load_id}` | Pricing strategy for a load (firm/moderate/flexible based on demand) |

### Negotiation
| Method | Endpoint | What it does |
|--------|----------|--------------|
| POST | `/api/v1/negotiate` | Deterministic pricing engine. Takes carrier offer, returns counter with ceiling-based logic. Max 3 rounds |

### Call Logging
| Method | Endpoint | What it does |
|--------|----------|--------------|
| POST | `/api/v1/calls` | Log a call. Auto-enriches with counter-offers from session, generates booking confirmation if booked |
| GET | `/api/v1/calls` | List calls with filters (outcome, sentiment, carrier, load) |

### Dashboard
| Method | Endpoint | What it does |
|--------|----------|--------------|
| GET | `/api/v1/dashboard/data` | Returns everything the dashboard needs in one request: KPIs, funnel, revenue, lanes, negotiation stats, experience |

### Other
| Method | Endpoint | What it does |
|--------|----------|--------------|
| POST | `/api/v1/carrier-preferences` | Save carrier lane/equipment preferences for unmet demand tracking |
| POST | `/api/v1/notifications/process-latest` | Post-call routing — triggers booking email or demand alert |
| POST | `/api/v1/loads/reset-all` | Reset all loads to available with fresh dates (for testing) |

---

## Dashboard

The dashboard is not just charts — it's a decision-making tool. It answers: "What should I do next?"

**What it shows:**
- **KPIs** — Calls handled, conversion rate, revenue booked, cost savings vs human reps
- **Conversion Funnel** — Where carriers drop off: verification, no loads, declined, pricing
- **Recent Deals** — Every booking with lane, listed rate, agreed rate, concession, and rounds
- **Negotiation Replay** — Line chart showing carrier asks vs agent counters converging to a deal
- **Lane Intelligence** — Which lanes convert best, which lanes carriers want but we don't have
- **Negotiation Performance** — Avg rounds to close, walk-away rate, gap on failed deals
- **Carrier Experience** — Sentiment breakdown, repeat caller rate, satisfaction score
- **Operational Efficiency** — Cost per booking, throughput, human rep equivalence
- **Scale Projector** — What happens at 50 or 200 calls/day (projected bookings, revenue, savings)
- **Recommended Actions** — Auto-generated, data-driven next steps (source inventory, adjust pricing, nurture carriers)

Every metric has a hover tooltip explaining what it means and how it's calculated.

The dashboard auto-refreshes every 30 seconds.

---

## Negotiation Engine

The AI doesn't guess prices. It uses a deterministic engine:

- **Opening rate** = loadboard rate (listed price)
- **Ceiling** = loadboard rate × (1 + stretch%). Stretch depends on demand: firm = +5%, moderate = +7%, flexible = +10%
- **Round 1**: Hold firm at opening rate
- **Round 2**: Stretch to ~40% of the range between opening and ceiling
- **Round 3**: Stretch to ~80% — near the ceiling
- **Absurd threshold**: If a carrier asks for 500%+ above rate, the engine rejects outright

The agent never invents a number. Every counter-offer comes from the engine.

---

## Voice Agent (HappyRobot)

The agent runs on the HappyRobot platform with a flat architecture — one prompt, seven tools at root level. No modules.

**Why flat?** HappyRobot modules lose context on transition. Since our flow needs carrier data (MC, name, DOT) throughout the entire call, a single prompt with all tools visible works better than chained modules.

**Tools:**
1. `verify_carrier` → FMCSA check
2. `check_carrier_history` → repeat caller detection
3. `search_loads` → find matching loads
4. `get_market_context` → pricing strategy
5. `evaluate_offer` → negotiation engine
6. `save_carrier_preferences` → save unmet demand
7. `log_call` → record outcome

**Post-call chain:**
Webhook → Condition (check if booked) → Gmail (send booking confirmation)

---

## Seed Data

The database comes with 10 pre-loaded loads covering common US freight lanes:

| Load ID | Lane | Equipment | Rate | Miles |
|---------|------|-----------|------|-------|
| LD-1001 | Dallas, TX → Atlanta, GA | Dry Van | $2,200 | 781 |
| LD-1002 | Chicago, IL → Miami, FL | Dry Van | $3,100 | 1,381 |
| LD-2001 | Fresno, CA → Denver, CO | Reefer | $3,400 | 1,086 |
| LD-2002 | Omaha, NE → Dallas, TX | Reefer | $2,800 | 661 |
| LD-2004 | Miami, FL → Atlanta, GA | Reefer | $1,900 | 662 |
| LD-3001 | Pittsburgh, PA → Detroit, MI | Flatbed | $1,800 | 289 |
| LD-3002 | Houston, TX → Oklahoma City, OK | Flatbed | $1,950 | 441 |
| LD-4001 | New York, NY → Chicago, IL | Dry Van | $3,800 | 790 |
| LD-4002 | San Francisco, CA → Seattle, WA | Dry Van | $2,400 | 808 |
| LD-4004 | Denver, CO → Salt Lake City, UT | Reefer | $2,100 | 525 |

Reset loads anytime: `POST /api/v1/loads/reset-all`

---

## Tech Stack

- **FastAPI** — async Python web framework
- **PostgreSQL** — database
- **SQLAlchemy** (async) — ORM
- **FMCSA QCMobile API** — real carrier verification
- **Chart.js** — dashboard charts
- **Tailwind CSS** — dashboard styling
- **Docker + Docker Compose** — containerization
- **Railway** — production hosting (auto-TLS, CI/CD from GitHub)
- **HappyRobot** — voice AI platform
- **Gmail API** — booking confirmation emails

---

## Testing

To run test calls:

1. Open the HappyRobot workflow and click the web call trigger
2. Use MC numbers: `166355`, `133655`, or `123456` (all real, FMCSA-active carriers)
3. Try different paths: accept immediately, negotiate, decline, ask for a lane we don't have

To reset data between test rounds:

```powershell
# Clear all call records
Invoke-RestMethod -Uri "https://happyrobot-fde-v2-production.up.railway.app/api/v1/calls" -Method DELETE -Headers @{"X-API-Key"="hr-prod-2025-secure"}

# Reset loads to available with fresh dates
Invoke-RestMethod -Uri "https://happyrobot-fde-v2-production.up.railway.app/api/v1/loads/reset-all" -Method POST -Headers @{"X-API-Key"="hr-prod-2025-secure"}
```

Or with curl:

```bash
# Clear calls
curl -X DELETE https://happyrobot-fde-v2-production.up.railway.app/api/v1/calls -H "X-API-Key: hr-prod-2025-secure"

# Reset loads
curl -X POST https://happyrobot-fde-v2-production.up.railway.app/api/v1/loads/reset-all -H "X-API-Key: hr-prod-2025-secure"
```

---

## Author

Antonio Gutierrez — FDE Technical Challenge submission for HappyRobot.