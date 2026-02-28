#!/usr/bin/env bash

set -euo pipefail

PORT="${1:-8000}"

if ! command -v ngrok >/dev/null 2>&1; then
  echo "ngrok is not installed or not on PATH."
  echo "Install it first: https://ngrok.com/download"
  exit 1
fi

echo "Starting ngrok tunnel to http://127.0.0.1:${PORT}"
echo "Use the shown https URL for:"
echo "  POST /api/feedback/submit_call_rating"
echo "  POST /api/feedback/submit_call_feedback"
echo ""
echo "Then set ElevenLabs tool call_id to {{system__conversation_id}}."
echo ""
ngrok http "$PORT"

