#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  printf '%s\n' ".env is missing. Run ./scripts/bootstrap.sh first."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  printf '%s\n' ".venv is missing. Run ./scripts/bootstrap.sh first."
  exit 1
fi

set -a
# shellcheck source=/dev/null
. ./.env
set +a

# kato.main embeds the planning webserver as a daemon thread in the same
# Python process so both share the live ClaudeSessionManager. Disable it
# entirely with KATO_WEBSERVER_DISABLED=true in .env.
exec .venv/bin/python -m kato.main
