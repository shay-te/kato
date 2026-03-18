#!/bin/sh
set -eu

cd /app

exec python -m openhands_agent.install
