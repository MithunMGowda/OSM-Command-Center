"""
Geospatial Operations Command Center — OSM edits monitoring dashboard.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.esri_boundaries import geojson_bbox, india_boundary_style, load_india_boundary_geojson  # noqa: E402
from src.osm_db import database_url, is_available, load_changesets as load_changesets_db, upsert_changesets  # noqa: E402
from src.osm_fetch import export_changesets_csv, fetch_team_changesets  # noqa: E402
from src.osm_ops_config import apply_mapper_start_filter, load_osm_ops_config, project_date_bounds  # noqa: E402
# export_changesets_csv used by enrich + sync buttons
from src.osm_ops_metrics import (  # noqa: E402
    DEFAULT_CHANGESETS_CSV,
    DEFAULT_ERRORS_CSV,
    apply_features_modified,
    build_daily_activity_counts,
    build_mapper_summary,
    changesets_with_errors,
    daily_activity_figure,
    demo_changesets,
    edit_hotspot_points,
    filter_changesets,
    global_kpis,
    highway_hierarchy_cell,
    hotspot_map_center,
    hotspot_map_zoom,
    load_changesets,
    load_errors,
)
from src.osmcha_client import (  # noqa: E402
    enrich_dataframe_with_osmcha,
    fetch_osmcha_dataframe,
    get_token,
    merge_osmcha_counts,
    test_connection,
)
from src.osm_exports import (  # noqa: E402
    export_csv_bytes,
    export_filename,
    export_geojson_bytes,
    export_per_mapper_zip,
)
from src.osm_live import live_feed_dataframe, poll_and_merge  # noqa: E402

st.set_page_config(
    page_title="OSM Operations Command Center",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .block-container { padding-top: 1.2rem; max-width: 1400px; }
    .ops-header {
        background: linear-gradient(135deg, #1e3a5f 0%, #2166ac 100%);
        color: white;
        padding: 1.25rem 1.5rem;
        border-radius: 10px;
        margin-bottom: 1rem;
    }
    .ops-header h1 { color: white !important; font-size: 1.75rem; margin: 0; }
    .ops-header p { color: #d4e4f7; margin: 0.35rem 0 0 0; font-size: 0.95rem; }
    div[data-testid="stMetric"] {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 0.65rem 0.75rem;
    }
    div[data-testid="stMetric"] label { font-size: 0.72rem !important; }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] { font-size: 1.35rem !important; }
    .insight-box {
        background: #f0f7ff;
        border-left: 4px solid #2166ac;
        padding: 0.75rem 1rem;
        border-radius: 0 8px 8px 0;
        margin-bottom: 0.5rem;
        font-size: 0.9rem;
    }
    .arch-box {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        padding: 1rem;
        border-radius: 8px;
        font-size: 0.85rem;
    }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="ops-header">
  <h1>Geospatial Operations Command Center</h1>
  <p>Track mapping productivity, road network contributions, edit quality, and team performance across geospatial operations.</p>
</div>
""",
    unsafe_allow_html=True,
)

def _apply_region_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Recompute region / derived columns after load or sync."""
    from src.osm_ops_metrics import load_changesets as _lc

    if frame.empty:
        return frame
    tmp = ROOT / ".cache" / "_dashboard_reload.csv"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(tmp, index=False)
    return _lc(tmp)


def _live_since_from_lookback(choice: str) -> pd.Timestamp:
    now = pd.Timestamp.utcnow()
    if choice == "Last 6 hours":
        return now - pd.Timedelta(hours=6)
    if choice == "Today (UTC)":
        return now.normalize()
    if choice == "Resume from last poll" and st.session_state.get("live_since") is not None:
        return pd.Timestamp(st.session_state.live_since, tz="UTC")
    return now - pd.Timedelta(hours=1)


def _init_live_tracking(base_raw: pd.DataFrame, lookback: str) -> None:
    if st.session_state.get("live_initialized"):
        return
    since = _live_since_from_lookback(lookback)
    st.session_state.live_since = since.to_pydatetime().replace(tzinfo=None)
    st.session_state.live_feed = pd.DataFrame()
    st.session_state.live_total_new = 0
    st.session_state.live_last_poll_at = None
    st.session_state.live_df = base_raw.copy() if not base_raw.empty else pd.DataFrame()
    st.session_state.live_initialized = True


def _should_poll_live() -> bool:
    last = st.session_state.get("live_last_poll_at")
    if last is None:
        return True
    interval = st.session_state.get("live_interval", 60)
    elapsed = (pd.Timestamp.utcnow() - pd.Timestamp(last)).total_seconds()
    return elapsed >= interval


def _poll_live_once(
    base_raw: pd.DataFrame,
    *,
    users: list[str],
    csv_path: str,
    append_csv: bool,
) -> pd.DataFrame:
    """Poll OSM when due; return the live dataset for filters and KPIs."""
    _init_live_tracking(base_raw, st.session_state.get("live_lookback", "Last 1 hour"))

    existing = st.session_state.get("live_df", base_raw)
    if not isinstance(existing, pd.DataFrame) or (existing.empty and not base_raw.empty):
        existing = base_raw

    if not _should_poll_live():
        return existing if isinstance(existing, pd.DataFrame) and not existing.empty else base_raw

    since = st.session_state.live_since
    csv_out = Path(csv_path) if append_csv else None
    merged, new_only, next_since = poll_and_merge(existing, users, since, csv_path=csv_out)
    st.session_state.live_since = next_since
    st.session_state.live_last_poll_at = pd.Timestamp.utcnow()
    merged = _apply_region_columns(merged)
    st.session_state.live_df = merged

    if not new_only.empty:
        new_only = _apply_region_columns(new_only)
        prev = st.session_state.get("live_feed", pd.DataFrame())
        st.session_state.live_feed = pd.concat([new_only, prev], ignore_index=True).drop_duplicates(
            subset=["changeset_id"], keep="first"
        )
        st.session_state.live_total_new = int(st.session_state.get("live_total_new", 0)) + len(new_only)

    return merged


def _resolve_raw_dataset(
    *,
    use_demo: bool,
    data_source: str,
    changesets_path: str,
    db_url_input: str,
    live_enabled: bool,
    live_lookback: str,
    live_append_csv: bool,
    sync_users: list[str],
) -> pd.DataFrame:
    """Load base data, then overlay live OSM poll when tracking is enabled."""
    if use_demo:
        base = demo_changesets()
    elif data_source == "PostgreSQL":
        if is_available(db_url_input):
            base = load_changesets_db(
                url=db_url_input,
                start_date=PROJECT_START.strftime("%Y-%m-%d"),
                end_date=TODAY_UTC.strftime("%Y-%m-%d"),
            )
            if not base.empty:
                base = _apply_region_columns(base)
        else:
            st.warning("PostgreSQL unavailable — using CSV fallback.")
            base = load_changesets(Path(changesets_path))
    else:
        base = load_changesets(Path(changesets_path))

    if not use_demo and not base.empty:
        base = apply_mapper_start_filter(base, OPS_CFG)

    if live_enabled:
        st.session_state.live_lookback = live_lookback
        try:
            return _poll_live_once(
                base,
                users=sync_users,
                csv_path=changesets_path,
                append_csv=live_append_csv,
            )
        except Exception as exc:
            st.error(f"Live poll failed: {exc}")
            return base

    return base


OPS_CFG = load_osm_ops_config()
PROJECT_START, PROJECT_END = project_date_bounds(OPS_CFG)
TODAY_UTC = pd.Timestamp.now("UTC").normalize()
filter_start = PROJECT_START
filter_end = max(TODAY_UTC, PROJECT_END or TODAY_UTC)

# --- Sidebar ---
with st.sidebar:
    st.header("Data source")
    data_source = st.radio(
        "Load from",
        ["CSV file", "PostgreSQL", "Live OSM API"],
        index=0,
    )

    use_demo = st.checkbox("Use demo sample data", value=False)
    changesets_path = st.text_input("Changesets CSV", value=str(DEFAULT_CHANGESETS_CSV))
    errors_path = st.text_input("OSMOSE errors CSV", value=str(DEFAULT_ERRORS_CSV))

    default_mappers = ["Pavang05", "ramshi04", "mitz01", "nagz02"]
    if "sync_users" not in st.session_state:
        st.session_state.sync_users = default_mappers

    st.divider()
    st.subheader("Live sync (OSM API)")
    sync_users = st.text_area(
        "Mapper usernames (one per line)",
        value="\n".join(st.session_state.sync_users),
        height=80,
    )
    st.session_state.sync_users = [u.strip() for u in sync_users.splitlines() if u.strip()]

    c1, c2 = st.columns(2)
    with c1:
        sync_start = st.date_input("Sync from", value=PROJECT_START.date())
    with c2:
        sync_end = st.date_input("Sync to", value=TODAY_UTC.date())

    skip_diffs = st.checkbox("Fast sync (skip diff counts)", value=False)
    save_to_db = st.checkbox("Save to PostgreSQL after sync", value=False)

    db_url_input = st.text_input(
        "Database URL",
        value=database_url() or "postgresql://osm:osm@localhost:5433/osm_ops",
        type="password",
    )

    st.divider()
    st.subheader("OSMCha QC")
    osmcha_token = st.text_input(
        "OSMCha API token",
        value=get_token() or "",
        type="password",
        help="Account settings at osmcha.org → API key",
    )
    merge_osmcha = st.checkbox("Merge OSMCha modified counts on sync", value=True)

    st.divider()
    st.subheader("Live edit tracking")
    live_enabled = st.toggle(
        "Enable live OSM edit tracking",
        value=st.session_state.get("live_enabled", False),
        help="Polls OSM API on an interval and streams new team changesets into the dashboard.",
    )
    st.session_state.live_enabled = live_enabled
    live_interval = st.select_slider(
        "Poll every (seconds)",
        options=[30, 60, 120, 300],
        value=st.session_state.get("live_interval", 60),
    )
    st.session_state.live_interval = live_interval
    live_lookback = st.selectbox(
        "Track edits since",
        ["Last 1 hour", "Last 6 hours", "Today (UTC)", "Resume from last poll"],
        index=0,
        disabled=not live_enabled,
    )
    live_append_csv = st.checkbox(
        "Append new edits to CSV",
        value=st.session_state.get("live_append_csv", True),
        disabled=not live_enabled,
    )
    st.session_state.live_append_csv = live_append_csv

    if live_enabled and st.button("Reset live tracker", use_container_width=True):
        for key in ("live_since", "live_feed", "live_df", "live_initialized", "live_last_poll_at", "live_total_new"):
            st.session_state.pop(key, None)
        st.rerun()

    if st.button("📊 Enrich OSMCha counts only", use_container_width=True):
        if not osmcha_token:
            st.error("OSMCha API token required.")
        else:
            try:
                base = load_changesets(Path(changesets_path))
                enriched = enrich_dataframe_with_osmcha(
                    base,
                    token=osmcha_token,
                    users=st.session_state.sync_users,
                    date_from=sync_start.isoformat(),
                    date_to=sync_end.isoformat(),
                )
                export_changesets_csv(enriched, changesets_path)
                st.session_state.raw_df = _apply_region_columns(enriched)
                st.success(
                    f"OSMCha enriched — {int(enriched['features_modified'].sum()):,} features modified total"
                )
            except Exception as exc:
                st.error(f"OSMCha enrich failed: {exc}")

    if st.button("🔄 Sync from OSM API", type="primary", use_container_width=True):
        progress = st.progress(0, text="Starting…")

        def on_progress(msg: str, frac: float):
            progress.progress(min(max(frac, 0.0), 1.0), text=msg)

        try:
            synced = fetch_team_changesets(
                st.session_state.sync_users,
                sync_start.isoformat(),
                sync_end.isoformat(),
                download_diffs=not skip_diffs,
                progress=on_progress,
            )
            if merge_osmcha and osmcha_token:
                cha = fetch_osmcha_dataframe(
                    token=osmcha_token,
                    users=st.session_state.sync_users,
                    date_from=sync_start.isoformat(),
                    date_to=sync_end.isoformat(),
                )
                synced = merge_osmcha_counts(synced, cha)
            else:
                synced = apply_features_modified(synced)
            synced = _apply_region_columns(synced)
            export_changesets_csv(synced, changesets_path)
            st.session_state.raw_df = synced
            if save_to_db and db_url_input:
                from src.osm_db import init_schema

                init_schema(db_url_input)
                upsert_changesets(synced, db_url_input)
            progress.progress(1.0, text=f"Done — {len(synced)} changesets")
            st.success(f"Saved {len(synced)} changesets → {changesets_path}")
        except Exception as exc:
            st.error(f"Sync failed: {exc}")

    if osmcha_token and st.button("Test OSMCha connection"):
        ok, msg = test_connection(osmcha_token)
        (st.success if ok else st.error)(msg)

    st.divider()
    st.header("Filters")

    live_active = live_enabled or data_source == "Live OSM API"
    if data_source == "Live OSM API" and not live_enabled:
        st.session_state.live_enabled = True

    raw = _resolve_raw_dataset(
        use_demo=use_demo,
        data_source=data_source,
        changesets_path=changesets_path,
        db_url_input=db_url_input,
        live_enabled=live_active,
        live_lookback=live_lookback,
        live_append_csv=live_append_csv,
        sync_users=st.session_state.sync_users,
    )

    if st.button("Reload from CSV", use_container_width=True):
        for key in ("raw_df", "live_df", "live_feed", "live_initialized", "live_last_poll_at"):
            st.session_state.pop(key, None)
        st.rerun()

    if raw.empty and not use_demo:
        st.warning("No data — run sync, enable live tracking, or use demo.")
        raw = demo_changesets()

    errors = load_errors(Path(errors_path))

    now_utc = pd.Timestamp.now("UTC")
    today_utc = now_utc.normalize()
    data_min = raw["created_at"].min() if not raw.empty else PROJECT_START
    data_max = raw["created_at"].max() if not raw.empty else today_utc
    if pd.isna(data_min):
        data_min = PROJECT_START
    if pd.isna(data_max):
        data_max = today_utc

    filter_start = max(PROJECT_START, data_min)
    filter_end = max(data_max, today_utc)
    default_start = filter_start
    default_end = today_utc

    if live_active:
        poll_at = st.session_state.get("live_last_poll_at")
        poll_txt = poll_at.strftime("%H:%M:%S UTC") if poll_at is not None else "pending"
        new_total = int(st.session_state.get("live_total_new", 0))
        st.markdown(
            f'<div class="insight-box">'
            f"<strong>LIVE DATA</strong> · {len(raw):,} changesets in view · "
            f"Last poll {poll_txt} · {new_total} new since tracking started · "
            f"Filters and KPIs use this dataset"
            f"</div>",
            unsafe_allow_html=True,
        )

    date_range = st.date_input(
        "Date range",
        value=(default_start.date(), default_end.date()),
        min_value=filter_start.date(),
        max_value=filter_end.date(),
        help="Defaults from project start through today (UTC).",
    )
    all_users = sorted(raw["user"].dropna().unique().tolist())
    selected_users = st.multiselect("Mappers", options=all_users, default=all_users)

    regions = ["All Regions"] + sorted(raw["region"].dropna().unique().tolist())
    region_filter = st.selectbox("Region", regions)

    st.caption(
        f"Data: **{'Live OSM API' if live_active else data_source}** · "
        f"Project start: **{PROJECT_START.date()}** · "
        f"Filter end defaults to **today (UTC)** · "
        "`config/osm_ops.yaml`"
    )


@st.fragment(run_every=timedelta(seconds=60))
def _live_tracking_panel() -> None:
    """Auto-refresh when live — re-polls OSM and reruns so filters pick up new edits."""
    if not st.session_state.get("live_enabled"):
        return

    feed = st.session_state.get("live_feed", pd.DataFrame())
    poll_at = st.session_state.get("live_last_poll_at")
    poll_txt = poll_at.strftime("%H:%M:%S UTC") if poll_at is not None else "—"
    new_total = int(st.session_state.get("live_total_new", 0))

    st.markdown(
        f'<div class="insight-box">'
        f"<strong>LIVE</strong> · Last poll {poll_txt} · "
        f"<strong>{new_total}</strong> new since tracking started · "
        f"Auto-refresh every ~{st.session_state.get('live_interval', 60)}s"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("#### Live edit feed")
    if feed.empty:
        st.caption("No new edits yet — waiting for the team to map…")
    else:
        st.dataframe(
            live_feed_dataframe(feed, limit=30),
            use_container_width=True,
            hide_index=True,
            height=min(320, 48 + min(len(feed), 30) * 36),
        )
        st.caption("Newest edits first · metadata-only poll (fast, no diff download)")

    # Trigger poll + full rerun so sidebar filters reload live data
    try:
        base = st.session_state.get("live_df", pd.DataFrame())
        before = len(base) if isinstance(base, pd.DataFrame) else 0
        _poll_live_once(
            base,
            users=st.session_state.sync_users,
            csv_path=st.session_state.get("changesets_path", str(DEFAULT_CHANGESETS_CSV)),
            append_csv=st.session_state.get("live_append_csv", True),
        )
        after = len(st.session_state.get("live_df", []))
        if after > before:
            st.rerun()
    except Exception as exc:
        st.error(f"Live poll failed: {exc}")


# Persist paths for fragment poll
st.session_state.changesets_path = changesets_path

if st.session_state.get("live_enabled"):
    _live_tracking_panel()

if isinstance(date_range, tuple) and len(date_range) >= 2:
    start_ts = pd.Timestamp(date_range[0], tz="UTC")
    end_ts = pd.Timestamp(date_range[1], tz="UTC")
elif hasattr(date_range, "year"):
    start_ts = pd.Timestamp(date_range, tz="UTC")
    end_ts = filter_end
else:
    start_ts = filter_start
    end_ts = filter_end

start_ts = max(start_ts, PROJECT_START)

df = filter_changesets(
    raw,
    start_date=start_ts,
    end_date=end_ts,
    users=selected_users if selected_users else None,
    region=region_filter,
)

active_cs_ids = set(df["changeset_id"].astype(str)) if not df.empty else set()
error_cs = changesets_with_errors(errors, active_cs_ids, changesets_df=df)
mapper_df = build_mapper_summary(df, error_cs)

kpis = global_kpis(df, mapper_df)

# --- KPI row ---
cols = st.columns(6)
metric_defs = [
    ("Total Contributions", f"{kpis.get('total_contributions', 0):,}", None),
    (
        "Features Added",
        f"{kpis.get('features_added', 0):,}",
        f"{kpis.get('changesets', 0):,} changesets",
    ),
    ("Features Modified", f"{kpis.get('features_modified', 0):,}", None),
    ("Features Removed", f"{kpis.get('features_removed', 0):,}", None),
    ("Active Mappers", str(kpis.get("active_mappers", 0)), None),
    ("Road Network Added", f"{kpis.get('road_km', 0)} km", None),
]
for col, (label, val, delta) in zip(cols, metric_defs):
    if delta:
        col.metric(label, val, delta, delta_color="off")
    else:
        col.metric(label, val)

cols2 = st.columns(6)
metric_defs2 = [
    ("Access Gates Added", str(kpis.get("gates", 0))),
    ("QC Approved", str(kpis.get("qc_approved", 0))),
    ("QC Rejected", str(kpis.get("qc_rejected", 0))),
    ("Quality Score", f"{kpis.get('quality_score', 100)}%"),
    ("Changesets", str(kpis.get("changesets", 0))),
    ("Building Polygons*", str(kpis.get("building_polygons", 0))),
]
for col, (label, val) in zip(cols2, metric_defs2):
    col.metric(label, val)

st.caption(
    "Added / Removed = OSM `created_count` & `deleted_count`. "
    "Modified = OSMCha `featMod` when enriched, else OSM `modified_count`. "
    "Road km = geometry from changeset diffs when computed (`road_km` column), else bbox estimate."
)

# --- Export filtered changesets ---
with st.expander("Download changeset data", expanded=False):
    st.caption(
        f"Exports respect current filters — **{len(df):,}** changesets · "
        f"{start_ts.date()} → {end_ts.date()}"
    )
    if df.empty:
        st.info("No changesets match the current filters.")
    else:
        csv_name = export_filename("changesets", start_ts, end_ts, "csv")
        geo_name = export_filename("changesets", start_ts, end_ts, "geojson")
        zip_name = export_filename("changesets_by_mapper", start_ts, end_ts, "zip")
        ex1, ex2, ex3 = st.columns(3)
        with ex1:
            st.download_button(
                "Download CSV (all)",
                data=export_csv_bytes(df),
                file_name=csv_name,
                mime="text/csv",
                use_container_width=True,
            )
        with ex2:
            st.download_button(
                "Download GeoJSON",
                data=export_geojson_bytes(df),
                file_name=geo_name,
                mime="application/geo+json",
                use_container_width=True,
                help="One polygon per changeset bounding box (or point if zero-area).",
            )
        with ex3:
            st.download_button(
                "Download CSV per mapper (ZIP)",
                data=export_per_mapper_zip(df),
                file_name=zip_name,
                mime="application/zip",
                use_container_width=True,
            )

# --- Edit hotspot map ---
st.subheader("Team edit hotspots")
map_ctrl, map_main = st.columns([1, 4])
with map_ctrl:
    heat_weight = st.selectbox(
        "Heat intensity",
        options=["activity", "changesets", "added", "road_km"],
        format_func=lambda x: {
            "activity": "Edit activity (log scale)",
            "changesets": "Changeset count",
            "added": "Features added",
            "road_km": "Road network (km)",
        }[x],
    )
    show_india_boundary = st.checkbox("India boundary (Esri)", value=True)
    st.caption(f"{len(df):,} changesets in view")

hotspot_pts = edit_hotspot_points(df, weight_by=heat_weight)
india_boundary = load_india_boundary_geojson() if show_india_boundary else None
with map_main:
    if hotspot_pts:
        center = hotspot_map_center(hotspot_pts)
        m = folium.Map(location=center, zoom_start=hotspot_map_zoom(hotspot_pts), tiles="CartoDB positron")
        if india_boundary:
            folium.GeoJson(
                india_boundary,
                name="India boundary (Esri Living Atlas)",
                style_function=india_boundary_style,
                tooltip=folium.GeoJsonTooltip(fields=["name"], aliases=["Country"]),
            ).add_to(m)
            india_box = geojson_bbox(india_boundary)
            if india_box:
                min_lat, min_lon, max_lat, max_lon = india_box
                folium.Rectangle(
                    bounds=[[min_lat, min_lon], [max_lat, max_lon]],
                    color="#64748b",
                    weight=1,
                    fill=False,
                    dash_array="6 4",
                    popup="India extent (Esri)",
                ).add_to(m)
        HeatMap(
            hotspot_pts,
            radius=14,
            blur=16,
            max_zoom=13,
            gradient={0.2: "#2166ac", 0.5: "#f59e0b", 0.8: "#dc2626", 1.0: "#7f1d1d"},
        ).add_to(m)
        st_folium(m, width=None, height=480, returned_objects=[])
        if not df.empty and "region" in df.columns:
            top_regions = df["region"].value_counts().head(3)
            esri_note = " · India outline: Esri Living Atlas India" if india_boundary else ""
            st.caption(
                "Hotspots from changeset bounding-box centroids · "
                + " · ".join(f"{r}: {n:,}" for r, n in top_regions.items())
                + esri_note
            )
    else:
        st.info("No geolocated changesets for the current filters.")

# --- Tabs ---
tab_ops, tab_qc, tab_build = st.tabs(
    ["Mapping Operations", "Quality Control", "Building Analytics"]
)

with tab_ops:
    st.subheader("Mapper Performance Summary")
    c1, c2 = st.columns([3, 1])
    with c1:
        search = st.text_input("Search mapper", placeholder="Filter by username…", label_visibility="collapsed")
    with c2:
        st.caption(f"Region: **{region_filter}**")

    if mapper_df.empty:
        st.info("No mapper data for the selected filters.")
    else:
        display = mapper_df.copy()
        if search:
            display = display[display["Mapper"].str.contains(search, case=False, na=False)]
        display["Highway Hierarchy"] = display["_highway"].apply(highway_hierarchy_cell)
        table_cols = [
            "Mapper",
            "Start date",
            "Features Added",
            "Changesets",
            "Features Modified",
            "Features Removed",
            "Total Contributions",
            "Road Network Added (km)",
            "Access Gates Added",
            "Highway Hierarchy",
            "Region",
        ]
        if "Start date" not in display.columns and "Mapper" in display.columns:
            from src.osm_ops_config import mapper_start_timestamp

            display["Start date"] = display["Mapper"].map(
                lambda m: mapper_start_timestamp(str(m)).strftime("%Y-%m-%d")
            )
        show_cols = [c for c in table_cols if c in display.columns]
        st.dataframe(
            display[show_cols],
            use_container_width=True,
            hide_index=True,
            height=min(420, 48 + len(display) * 38),
        )

    st.subheader("Changeset activity")
    if not df.empty:
        activity_users = sorted(df["user"].dropna().unique().tolist())
        default_activity = [u for u in (selected_users or activity_users) if u in activity_users]
        chart_mappers = st.multiselect(
            "Mappers (activity chart)",
            options=activity_users,
            default=default_activity or activity_users,
            key="activity_chart_mappers",
        )
        if not chart_mappers:
            st.info("Select at least one mapper to show daily activity.")
        else:
            activity_counts = build_daily_activity_counts(df, chart_mappers)
            st.pyplot(daily_activity_figure(activity_counts), use_container_width=True, clear_figure=True)
            st.caption("Last 10 days · one bar per mapper per day · Y-axis 0–200 changesets")

        with st.expander("Raw changeset log"):
            log_cols = [
                "user",
                "changeset_id",
                "created_at",
                "comment",
                "total_created",
                "features_modified",
                "osmcha_feat_mod",
                "total_deleted",
                "region",
            ]
            st.dataframe(df[log_cols].sort_values("created_at", ascending=False), use_container_width=True)

with tab_qc:
    st.subheader("Quality control overview")
    q1, q2, q3 = st.columns(3)
    q1.metric("QC Approved (changesets)", kpis.get("qc_approved", 0))
    q2.metric("QC Rejected (flagged)", kpis.get("qc_rejected", 0))
    q3.metric("Quality score", f"{kpis.get('quality_score', 100)}%")

    st.markdown(
        """
        **Features Modified** uses OSMCha `featMod` when enriched; OSM diff `total_modified` is shown in the raw log only.
        QC flags combine **OSMOSE** errors and **OSMCha** `is_suspect`.
        """
    )

    if "osmcha_suspect" in df.columns:
        flagged = df[df["osmcha_suspect"].fillna(False).astype(bool)]
        if not flagged.empty:
            st.subheader("OSMCha flagged changesets")
            cha_cols = ["user", "changeset_id", "created_at", "comment", "osmcha_reasons"]
            show = [c for c in cha_cols if c in flagged.columns]
            st.dataframe(
                flagged[show].sort_values("created_at", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

    if not mapper_df.empty:
        qc_table = mapper_df[["Mapper", "Changesets", "QC Approved", "QC Rejected", "Region"]].copy()
        qc_table["Pass rate %"] = qc_table.apply(
            lambda r: round(100 * r["QC Approved"] / r["Changesets"], 1) if r["Changesets"] else 100,
            axis=1,
        )
        st.dataframe(qc_table, use_container_width=True, hide_index=True)

    if not errors.empty:
        st.subheader("OSMOSE issues (sample)")
        err_show = errors.copy()
        if "in_may_25_26" in err_show.columns:
            err_show = err_show[err_show["in_may_25_26"].astype(str).str.lower() == "true"]
        st.dataframe(
            err_show[["changeset_id", "timestamp", "error_type", "elem_type", "elem_id"]].head(100),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Add `ramshi04_errors_by_changeset.csv` (or a team-wide export) for detailed QC errors.")

with tab_build:
    st.subheader("Building analytics")
    st.markdown(
        "Building footprint tagging is not fully extracted from changeset summaries. "
        "Use **Osmium** or **OSMCha** diffs for `building=*` polygon counts and area."
    )
    b1, b2 = st.columns(2)
    b1.metric("Building-related changesets", int(df["has_building"].sum()) if "has_building" in df.columns else 0)
    b2.metric("Total ways created", int(df["ways_created"].sum()) if not df.empty else 0)

# --- Footer panels ---
st.divider()
foot_l, foot_r = st.columns(2)
with foot_l:
    st.markdown("#### Operational insights")
    for tip in [
        "Track mapper productivity daily from changeset exports.",
        "Monitor DELETE actions carefully — spikes may indicate accidental bulk deletes.",
        "Review suspicious bulk edits and disconnected highways (OSMOSE 1260/1270).",
        "Validate topology and routing edits after tertiary/trunk changes.",
    ]:
        st.markdown(f'<div class="insight-box">{tip}</div>', unsafe_allow_html=True)

with foot_r:
    st.markdown("#### System architecture")
    st.markdown(
        """
<div class="arch-box">
<strong>Data source:</strong> OSM API · OSMCha API · OSMOSE · PostgreSQL<br>
<strong>Sync:</strong> <code>scripts/sync_osm_ops.py</code> or sidebar **Sync from OSM API**<br>
<strong>Live:</strong> sidebar **Enable live edit tracking** or <code>scripts/live_osm_tracker.py</code><br>
<strong>Database:</strong> <code>docker compose -f docker-compose.osm-ops.yml up -d</code><br>
<strong>Cron:</strong> Daily <code>sync_osm_ops.py</code> with <code>OSM_OPS_DATABASE_URL</code>
</div>
""",
        unsafe_allow_html=True,
    )
