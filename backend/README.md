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
