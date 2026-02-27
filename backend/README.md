# Backend

Minimal FastAPI backend for Dormero Viktoria Monitoring V1.

## Setup

```bash
cd /Users/peter/clar/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Configure environment values in:

- `/Users/peter/clar/backend/.env`

Required keys:

- `ELEVENLABS_API_KEY`
- `OPENAI_API_KEY`

Optional:

- `OPENAI_MODEL` (defaults to `gpt-4.1-mini`)
- `SMART_INSIGHTS_DETAIL_CAP` (defaults to `200`)
- `SMART_INSIGHTS_DETAIL_FETCH_CONCURRENCY` (defaults to `8`)

Smart Insights retrieval now uses conversation list pagination to find the window, then fetches per-conversation details
for analyzed calls (details contain `analysis.data_collection_results` and `analysis.evaluation_criteria_results`).
If detail fetch fails for a conversation, the endpoint falls back to summary payload for that record and reports a caveat.

Smart Insights V2 response shape is designed for non-technical support agents:

- `meta`
- `overview` (executive summary + top opportunity)
- `knowledgeGapInsights` (top 3 patterns with gap, friction, and internal action)
- `failureTypeInsights` (top 3 failure types with plain-language why)
- `priorityActionQueue` (top 3 with what/why/next step/escalation trigger)
- `caveats` (bullet points only)

Weighted criteria framing is removed from report output and ranking.

Set the customer agent id in:

- `/Users/peter/clar/backend/app/config/customer_agent.py`

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API endpoints:

- `GET /health`
- `GET /api/monitoring/conversations?cursor=&pageSize=`
- `GET /api/statistics/overview?timeline=1d&currency=USD`
- `GET /api/smart-insights/report?timeline=7d`
