#!/usr/bin/env bash
set -euo pipefail

# Only run in Claude Code remote (web) sessions
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# ── Install Python dependencies ──────────────────────────────────────────────
poetry install --no-interaction 2>&1

# ── Write .env file from environment variables ───────────────────────────────
# These variables should be configured in your Claude Code project settings.
# The codebase loads .env from src/fofproject/.env via python-dotenv.
ENV_FILE="$CLAUDE_PROJECT_DIR/src/fofproject/.env"

: > "$ENV_FILE"  # create or truncate

[ -n "${OPENAI_API_KEY:-}" ]  && echo "OPENAI_API_KEY=$OPENAI_API_KEY"   >> "$ENV_FILE"
[ -n "${TENANT_ID:-}" ]       && echo "TENANT_ID=$TENANT_ID"             >> "$ENV_FILE"
[ -n "${CLIENT_ID:-}" ]       && echo "CLIENT_ID=$CLIENT_ID"             >> "$ENV_FILE"
[ -n "${ID_NUMBER:-}" ]       && echo "ID_NUMBER=$ID_NUMBER"             >> "$ENV_FILE"
[ -n "${CAR_NUMBER:-}" ]      && echo "CAR_NUMBER=$CAR_NUMBER"           >> "$ENV_FILE"
[ -n "${EMAIL:-}" ]           && echo "EMAIL=$EMAIL"                     >> "$ENV_FILE"
[ -n "${NOTION_TOKEN:-}" ]    && echo "NOTION_TOKEN=$NOTION_TOKEN"       >> "$ENV_FILE"
[ -n "${INVESTOR_EMAIL:-}" ]  && echo "INVESTOR_EMAIL=$INVESTOR_EMAIL"   >> "$ENV_FILE"

echo "session-start: dependencies installed, .env written to $ENV_FILE"
