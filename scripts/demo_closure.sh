#!/usr/bin/env bash
# Reproduce one closure → one reroute.
# Prerequisite: docker compose up -d   (image falkordb/falkordb:v4.22.0)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  exec "${ROOT}/.venv/bin/python" -m floodline.demo "$@"
fi
exec python3 -m floodline.demo "$@"
