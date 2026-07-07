# Geospatial Command Center

Operations dashboard for OSM mapping teams: productivity KPIs, edit hotspots, QC flags (OSMCha / OSMOSE), live OSM polling, and optional PostgreSQL storage.

## Quick start (local)

```bash
cd "Geospatial Command Center"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # optional: OSMCHA_TOKEN, database URL
# Edit config/osm_ops.yaml — mappers, start_date, mapper_start_dates

python3 scripts/sync_osm_ops.py --no-diffs --no-osmcha
./scripts/start.sh
```

Open **http://localhost:8501**

## Configuration

| File | Purpose |
|------|---------|
| `config/osm_ops.yaml` | Mappers, project start dates, CSV path |
| `.env` | `OSMCHA_TOKEN`, `OSM_OPS_DATABASE_URL` |

Data files live under `data/` (not committed — sync to generate).

## Scripts

```bash
# Full metadata sync (fast)
python3 scripts/sync_osm_ops.py --no-diffs --no-osmcha

# Road network km (heuristic)
python3 scripts/compute_road_km.py --heuristic-only

# Live polling CLI
python3 scripts/live_osm_tracker.py --users Pavang05 ramshi04
```

## Docker (internal hosting)

```bash
cp .env.example .env
docker compose up -d --build
```

- Dashboard: http://localhost:8501  
- PostgreSQL: `postgresql://osm:osm@localhost:5433/osm_ops`

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for production notes.

## Push to GitHub

```bash
git init
git add .
git commit -m "Initial Geospatial Command Center release"
git remote add origin git@github.com:YOUR_ORG/geospatial-command-center.git
git push -u origin main
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
