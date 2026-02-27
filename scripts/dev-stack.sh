#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

BACKEND_PID_FILE="/tmp/clar-backend.pid"
FRONTEND_PID_FILE="/tmp/clar-frontend.pid"
BACKEND_LOG_FILE="/tmp/clar-backend.log"
FRONTEND_LOG_FILE="/tmp/clar-frontend.log"
BACKEND_REQUIREMENTS_FILE="$BACKEND_DIR/requirements.txt"
BACKEND_REQUIREMENTS_STAMP="$BACKEND_DIR/.venv/.requirements.sha256"
BACKEND_PORT="8000"

usage() {
  cat <<USAGE
Usage: ./scripts/dev-stack.sh {start|restart|quit}

Commands:
  start    Start backend and frontend dev servers
  restart  Restart both servers
  quit     Stop both servers
USAGE
}

is_running() {
  local pid_file="$1"

  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi

  local pid
  pid="$(cat "$pid_file")"

  if [[ -z "$pid" ]]; then
    return 1
  fi

  kill -0 "$pid" 2>/dev/null
}

port_listener_pid() {
  local port="$1"
  (lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null | head -n 1) || true
}

assert_backend_port_available() {
  local occupied_pid
  occupied_pid="$(port_listener_pid "$BACKEND_PORT")"

  if [[ -z "$occupied_pid" ]]; then
    return
  fi

  local recorded_pid=""
  if [[ -f "$BACKEND_PID_FILE" ]]; then
    recorded_pid="$(cat "$BACKEND_PID_FILE")"
  fi

  if [[ -n "$recorded_pid" && "$recorded_pid" == "$occupied_pid" ]] && kill -0 "$recorded_pid" 2>/dev/null; then
    return
  fi

  local process_command
  process_command="$(ps -p "$occupied_pid" -o command= 2>/dev/null || echo "unknown")"

  echo "Backend port $BACKEND_PORT is already in use by PID $occupied_pid."
  echo "Process: $process_command"
  echo "Stop that process first, or change your local setup to free port $BACKEND_PORT."
  echo "If it is your previous dev process, run: kill $occupied_pid"
  exit 1
}

requirements_hash() {
  local file_path="$1"

  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file_path" | awk '{print $1}'
    return
  fi

  python3 - "$file_path" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY
}

ensure_backend_dependencies() {
  if [[ ! -f "$BACKEND_REQUIREMENTS_FILE" ]]; then
    echo "Missing backend requirements file: $BACKEND_REQUIREMENTS_FILE"
    exit 1
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required but was not found on PATH."
    exit 1
  fi

  if [[ ! -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    echo "Creating backend virtualenv..."
    (
      cd "$BACKEND_DIR"
      python3 -m venv .venv
    )
  fi

  local current_hash=""
  local previous_hash=""
  local deps_import_check=0

  current_hash="$(requirements_hash "$BACKEND_REQUIREMENTS_FILE")"

  if [[ -f "$BACKEND_REQUIREMENTS_STAMP" ]]; then
    previous_hash="$(cat "$BACKEND_REQUIREMENTS_STAMP")"
  fi

  if "$BACKEND_DIR/.venv/bin/python" -c "import uvicorn, fastapi, httpx, dotenv" >/dev/null 2>&1; then
    deps_import_check=1
  fi

  if [[ "$current_hash" == "$previous_hash" && "$deps_import_check" -eq 1 ]]; then
    return
  fi

  echo "Syncing backend dependencies from requirements.txt..."
  if ! (
    cd "$BACKEND_DIR"
    .venv/bin/python -m pip install -r requirements.txt
  ); then
    echo "Failed to install backend dependencies."
    echo "Check network access and retry: cd $BACKEND_DIR && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
  fi

  echo "$current_hash" >"$BACKEND_REQUIREMENTS_STAMP"
}

start_backend() {
  if is_running "$BACKEND_PID_FILE"; then
    echo "Backend already running (PID: $(cat "$BACKEND_PID_FILE"))."
    return
  fi

  ensure_backend_dependencies
  assert_backend_port_available

  echo "Starting backend..."
  (
    cd "$BACKEND_DIR"
    nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload >"$BACKEND_LOG_FILE" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )

  sleep 1
  if is_running "$BACKEND_PID_FILE"; then
    echo "Backend started (PID: $(cat "$BACKEND_PID_FILE"))."
  else
    echo "Backend failed to start. Check log: $BACKEND_LOG_FILE"
    tail -n 30 "$BACKEND_LOG_FILE" || true
    exit 1
  fi
}

start_frontend() {
  if is_running "$FRONTEND_PID_FILE"; then
    echo "Frontend already running (PID: $(cat "$FRONTEND_PID_FILE"))."
    return
  fi

  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    echo "Frontend dependencies missing. Install them first:"
    echo "  cd $FRONTEND_DIR && npm install"
    exit 1
  fi

  echo "Starting frontend..."
  (
    cd "$FRONTEND_DIR"
    nohup npm run dev >"$FRONTEND_LOG_FILE" 2>&1 &
    echo $! >"$FRONTEND_PID_FILE"
  )

  sleep 1
  if is_running "$FRONTEND_PID_FILE"; then
    echo "Frontend started (PID: $(cat "$FRONTEND_PID_FILE"))."
  else
    echo "Frontend failed to start. Check log: $FRONTEND_LOG_FILE"
    exit 1
  fi
}

stop_process() {
  local name="$1"
  local pid_file="$2"

  if [[ ! -f "$pid_file" ]]; then
    echo "$name is not running."
    return
  fi

  local pid
  pid="$(cat "$pid_file")"

  if [[ -z "$pid" ]]; then
    rm -f "$pid_file"
    echo "$name is not running."
    return
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file"
    echo "$name is not running."
    return
  fi

  echo "Stopping $name (PID: $pid)..."
  kill "$pid" 2>/dev/null || true

  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      echo "$name stopped."
      return
    fi
    sleep 0.25
  done

  echo "$name did not stop gracefully; forcing kill..."
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$pid_file"
  echo "$name stopped."
}

start_all() {
  start_backend
  start_frontend
  echo ""
  echo "Logs:"
  echo "  Backend:  $BACKEND_LOG_FILE"
  echo "  Frontend: $FRONTEND_LOG_FILE"
}

quit_all() {
  stop_process "Frontend" "$FRONTEND_PID_FILE"
  stop_process "Backend" "$BACKEND_PID_FILE"
}

restart_all() {
  quit_all
  start_all
}

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

case "$1" in
  start)
    start_all
    ;;
  restart)
    restart_all
    ;;
  quit)
    quit_all
    ;;
  *)
    usage
    exit 1
    ;;
esac
