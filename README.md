# Dormero Viktoria Monorepo

Frontend-first monorepo for the Viktoria control center, now including Monitoring V1 backend integration.

## Current Structure

- `frontend/` - React + Vite + TypeScript app with sidebar, redesigned home, and Monitoring V1 list UI.
- `backend/` - Python FastAPI backend that proxies ElevenLabs conversations for Monitoring V1.
- `database/` - Placeholder for future Prisma/PostgreSQL implementation.

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
- Backend endpoint `GET /api/monitoring/conversations` for the configured customer agent.
- Backend endpoint `GET /api/smart-insights/report?timeline=1d|7d|1m` with OpenAI structured report generation.
- Centralized styling in `frontend/src/styles/theme.css`.
