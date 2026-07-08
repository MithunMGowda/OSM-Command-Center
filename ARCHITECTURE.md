# Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Streamlit UI — app/osm_ops_dashboard.py                    │
│  KPIs · mapper table · hotspot map · live feed · QC tabs    │
└───────────────┬─────────────────────────────────────────────┘
                │
    ┌───────────┼───────────┬──────────────┬──────────────┐
    ▼           ▼           ▼              ▼              ▼
 osm_ops_    osm_ops_    osm_fetch     osmcha_        esri_
 metrics     config       osm_live      client         boundaries
    │           │           │              │              │
    └───────────┴───────────┴──────────────┴──────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
        data/*.csv                  PostgreSQL
        (default store)            (optional warehouse)
              ▲
              │
        scripts/sync_osm_ops.py
        scripts/live_osm_tracker.py
```

## Data flow

1. **Sync** — `sync_osm_ops.py` pulls OSM changeset metadata (and optional diffs) per mapper → `data/osm_changesets_with_counts.csv`
2. **Enrich** — OSMCha API merges `featMod` / suspect flags when token is set
3. **Metrics** — `osm_ops_metrics.py` aggregates KPIs, regions, road km estimates
4. **Live** — `osm_live.py` polls OSM for edits since last watermark (dashboard or CLI)
5. **Display** — Folium heatmap from changeset bboxes; Esri India boundary overlay

## Key modules

| Module | Role |
|--------|------|
| `src/osm_fetch.py` | OSM API client, pagination, diff counts |
| `src/osm_ops_config.py` | YAML config, per-mapper start dates |
| `src/osm_ops_metrics.py` | Dashboard aggregations |
| `src/osm_db.py` | PostgreSQL upsert / load |
| `src/road_km.py` | Road length from diffs or bbox heuristic |
| `src/esri_boundaries.py` | India boundary GeoJSON (cached) |

## Filters

- Project **start** from `config/osm_ops.yaml`
- Filter **end** defaults to **today (UTC)**
- Per-mapper `mapper_start_dates` trim history before each user’s onboarding date
