"""Fetch India administrative boundary from Esri Living Atlas (India)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / ".cache" / "india_boundary_esri.geojson"
CACHE_TTL_SECONDS = 30 * 24 * 3600

ESRI_INDIA_BOUNDARY_URL = (
    "https://livingatlas.esri.in/server/rest/services/"
    "IAB2024/India_Administrative_Boundaries_2024/MapServer/0/query"
)
ESRI_QUERY_PARAMS = {
    "where": "1=1",
    "outSR": "4326",
    "returnGeometry": "true",
    "outFields": "name",
    "f": "geojson",
    # Simplify geometry so GeoJSON export succeeds and loads quickly in Folium.
    "maxAllowableOffset": "0.05",
    "geometryPrecision": "5",
}


def _walk_coords(coords: Any, points: list[list[float]]) -> None:
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
        points.append([float(coords[0]), float(coords[1])])
        return
    if isinstance(coords, (list, tuple)):
        for part in coords:
            _walk_coords(part, points)


def geojson_bbox(geojson: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Return (min_lat, min_lon, max_lat, max_lon) for a GeoJSON FeatureCollection."""
    points: list[list[float]] = []
    for feat in geojson.get("features", []):
        _walk_coords(feat.get("geometry", {}).get("coordinates"), points)
    if not points:
        return None
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return min(lats), min(lons), max(lats), max(lons)


def fetch_india_boundary_geojson() -> dict[str, Any]:
    """Download India country boundary GeoJSON from Esri Living Atlas India."""
    resp = requests.get(
        ESRI_INDIA_BOUNDARY_URL,
        params=ESRI_QUERY_PARAMS,
        timeout=90,
        verify=False,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("features"):
        raise RuntimeError("Esri India boundary query returned no features")
    return data


def load_india_boundary_geojson(force_refresh: bool = False) -> dict[str, Any] | None:
    """Load cached India boundary or fetch from Esri."""
    if not force_refresh and CACHE_PATH.exists():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            try:
                with CACHE_PATH.open() as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    try:
        data = fetch_india_boundary_geojson()
    except Exception:
        if CACHE_PATH.exists():
            with CACHE_PATH.open() as f:
                return json.load(f)
        return None

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("w") as f:
        json.dump(data, f)
    return data


def india_boundary_style(_feature: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "color": "#1e3a5f",
        "weight": 2,
        "fillColor": "#93c5fd",
        "fillOpacity": 0.06,
        "dashArray": "4 4",
    }
