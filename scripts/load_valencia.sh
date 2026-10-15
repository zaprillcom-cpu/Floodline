#!/usr/bin/env bash
# Load the Valencia flood-corridor drive network and prove one CCH detour.
# Requires FalkorDB v4.22.0 from `docker compose up -d`.
# Pass --refresh to re-download the bbox with OSMnx (Overpass). Otherwise the
# committed extract at backend/data/osm/valencia_corridor.json is used.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-backend}"
python -m floodline.osm_demo "$@"
