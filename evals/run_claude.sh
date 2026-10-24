#!/bin/bash
# Records a run in which Claude Code (a small model, headless) plays the assistant against the live deployment.
# Needs: the stack running (docker compose up -d), `claude` logged in, and PUBLIC_BASE_URL + OFFICER_LOGINS in .env.
set -e
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
VENV="$HOME/.venvs/sewasetu-evals"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -r mcp_service/requirements.txt
fi
export SEWASETU_CONFIG="$PWD/deploy/host.ini"
export PORTAL_URL="${PUBLIC_BASE_URL:-http://localhost}"
export SMS_URL="$PORTAL_URL/__gateway"
export EVAL_MODEL="${EVAL_MODEL:-haiku}"
exec "$VENV/bin/python" evals/runner.py --claude "$@"
