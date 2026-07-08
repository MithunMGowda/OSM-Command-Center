# Deployment guide — Geospatial Command Center

## Prerequisites

- Python 3.11+ or Docker
- Outbound HTTPS to `api.openstreetmap.org`, `osmcha.org` (optional), `livingatlas.esri.in` (India boundary map)
- Optional: PostgreSQL 16

## 1. Local / VM hosting

```bash
./scripts/start.sh
```

Default port **8501**. Override:

```bash
streamlit run app/osm_ops_dashboard.py --server.port 8504
```

### Daily data sync (cron)

```bash
0 6 * * * cd "/path/to/Geospatial Command Center" && \
  .venv/bin/python scripts/sync_osm_ops.py --no-diffs >> logs/sync.log 2>&1
```

## 2. Docker Compose

```bash
docker compose up -d --build
```

Volumes:

- `./data` — changeset CSV exports
- `./config` — read-only mapper config
- `pgdata` — PostgreSQL persistence

Environment (`.env`):

```env
OSM_OPS_DATABASE_URL=postgresql://osm:osm@db:5432/osm_ops
OSMCHA_TOKEN=your-token
```

## 3. Internal reverse proxy (example)

Place behind nginx with TLS:

```nginx
location /gcc/ {
    proxy_pass http://127.0.0.1:8501/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```

Set Streamlit base URL if needed in `.streamlit/config.toml`:

```toml
[server]
baseUrlPath = "gcc"
```

## 4. Secrets

Never commit `.env` or CSV exports with production data. Use your org’s secret store for `OSMCHA_TOKEN` and database credentials.

## 5. First-time checklist

1. Edit `config/osm_ops.yaml` (mappers, `start_date`)
2. Run `python3 scripts/sync_osm_ops.py --no-diffs`
3. Optional: set `OSMCHA_TOKEN` and enrich via dashboard sidebar
4. Optional: `python3 scripts/compute_road_km.py --heuristic-only`
5. Start dashboard and verify KPIs + hotspot map
