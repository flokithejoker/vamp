# Dormero Viktoria Monorepo

Frontend-first monorepo for the Viktoria control center, now including Monitoring V1 backend integration.

## Current Structure

- `frontend/` - React + Vite + TypeScript app with sidebar, redesigned home, and Monitoring V1 list UI.
- `backend/` - Python FastAPI backend that proxies ElevenLabs conversations for Monitoring V1.
- `database/` - Local SQLite storage (currently used for call feedback data).

## Run Frontend

```bash
cd frontend
npm install
npm run dev
```

## Run Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Set customer agent config in:

- `/Users/peter/clar/backend/app/config/customer_agent.py`

## Dev Script

Use the combined dev stack script:

```bash
./scripts/dev-stack.sh start
./scripts/dev-stack.sh restart
./scripts/dev-stack.sh quit
```

## Current Scope

- Sidebar navigation for all required pages.
- Home landing page with redesigned action cards.
- Monitoring page with scrollable conversation list and load-more pagination.
- Feedback page with persisted call ratings/comments linked to conversations.
- Backend endpoint `GET /api/monitoring/conversations` for the configured customer agent.
- Backend endpoints for feedback ingestion:
  - `POST /api/feedback/submit_call_rating`
  - `POST /api/feedback/submit_call_feedback`
- Backend endpoint `GET /api/smart-insights/report?timeline=1d|7d|1m` with detail-level ElevenLabs analysis extraction, bounded detail cap, and OpenAI structured report generation.
- Smart Insights UI rendered as a single-sheet plain-language report for support agents (executive summary, top opportunity, knowledge-gap insights, failure types, priority action queue, and caveats).
- Centralized styling in `frontend/src/styles/theme.css`.

## Feedback Tool Wiring (Local Test)

1. Start backend and frontend:

```bash
./scripts/dev-stack.sh start
```

2. Start ngrok tunnel for backend:

```bash
./scripts/start-ngrok.sh
```

3. In ElevenLabs, configure tools with your ngrok base URL:

- `submit_call_rating` -> `POST <NGROK_URL>/api/feedback/submit_call_rating`
- `submit_call_feedback` -> `POST <NGROK_URL>/api/feedback/submit_call_feedback`
- `call_id` must be `{{system__conversation_id}}`

This local test setup intentionally uses no authentication on feedback endpoints.
