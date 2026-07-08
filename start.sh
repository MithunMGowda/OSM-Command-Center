#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

mkdir -p data logs .cache

if [[ ! -f data/osm_changesets_with_counts.csv ]]; then
  echo "No data yet — run: python3 scripts/sync_osm_ops.py --no-diffs --no-osmcha"
fi

exec streamlit run app/osm_ops_dashboard.py --server.port 8501
