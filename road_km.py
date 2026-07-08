"""Compute road network length (km) from OSM changeset diffs."""

from __future__ import annotations

import math
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OSM_API = "https://api.openstreetmap.org/api/0.6"

ROAD_COMMENT_KEYWORDS = (
    "road",
    "highway",
    "gate",
    "link",
    "tertiary",
    "residential",
    "service",
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "footway",
    "track",
    "unclassified",
    "roundabout",
    "access",
)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def is_road_related_comment(comment: str) -> bool:
    text = (comment or "").lower()
    return any(k in text for k in ROAD_COMMENT_KEYWORDS)


def bbox_diagonal_km(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> float:
    """Approximate span of changeset bbox in km."""
    mid_lat = (min_lat + max_lat) / 2
    mid_lon = (min_lon + max_lon) / 2
    width = haversine_km(mid_lat, min_lon, mid_lat, max_lon)
    height = haversine_km(min_lat, mid_lon, max_lat, mid_lon)
    return math.sqrt(width * width + height * height)


def estimate_road_km_bbox(
    comment: str,
    min_lat: Any,
    min_lon: Any,
    max_lat: Any,
    max_lon: Any,
    total_created: int = 0,
) -> float:
    """
    Fast estimate when diff geometry is unavailable.
    Road edits rarely fill the whole bbox — scale diagonal down.
    """
    if not is_road_related_comment(comment) and total_created <= 0:
        return 0.0
    try:
        la1, lo1, la2, lo2 = float(min_lat), float(min_lon), float(max_lat), float(max_lon)
    except (TypeError, ValueError):
        return 0.05 if is_road_related_comment(comment) else 0.0
    if any(math.isnan(v) for v in (la1, lo1, la2, lo2)):
        return 0.05 if is_road_related_comment(comment) else 0.0
    if la1 == la2 and lo1 == lo2:
        return 0.05 if is_road_related_comment(comment) else 0.0
    diag = bbox_diagonal_km(la1, lo1, la2, lo2)
    scale = 0.35 if is_road_related_comment(comment) else 0.15
    # Cap heuristic per changeset (large bboxes are often whole-city uploads)
    km = max(0.02, diag * scale)
    return round(min(km, 2.0), 3)


def road_km_from_diff_xml(xml_text: str, *, include_modified: bool = False) -> float:
    """
    Sum segment lengths of highway ways in an OsmChange diff.
    Counts created ways; optionally modified ways when include_modified=True.
    """
    root = ET.fromstring(xml_text)
    nodes: dict[str, tuple[float, float]] = {}

    for action in root:
        if action.tag not in ("create", "modify"):
            continue
        for elem in action:
            if elem.tag != "node":
                continue
            lat, lon = elem.get("lat"), elem.get("lon")
            if lat is not None and lon is not None:
                nodes[elem.get("id", "")] = (float(lat), float(lon))

    total_km = 0.0
    actions = ("create",) if not include_modified else ("create", "modify")
    for action in root:
        if action.tag not in actions:
            continue
        for elem in action.findall("way"):
            tags = {t.get("k"): t.get("v") for t in elem.findall("tag")}
            if "highway" not in tags:
                continue
            refs = [nd.get("ref") for nd in elem.findall("nd")]
            for i in range(1, len(refs)):
                a, b = refs[i - 1], refs[i]
                if a in nodes and b in nodes:
                    lat1, lon1 = nodes[a]
                    lat2, lon2 = nodes[b]
                    total_km += haversine_km(lat1, lon1, lat2, lon2)

    return round(total_km, 3)


def road_km_from_changeset(
    changeset_id: str,
    session: requests.Session | None = None,
    *,
    include_modified: bool = False,
) -> float:
    session = session or requests.Session()
    session.verify = False
    url = f"{OSM_API}/changeset/{changeset_id}/download"
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    return road_km_from_diff_xml(resp.text, include_modified=include_modified)


def compute_road_km_batch(
    changeset_ids: list[str],
    *,
    delay_sec: float = 0.25,
    progress: Any = None,
) -> dict[str, float]:
    session = requests.Session()
    session.verify = False
    results: dict[str, float] = {}
    n = len(changeset_ids)
    for i, cs_id in enumerate(changeset_ids):
        try:
            results[str(cs_id)] = road_km_from_changeset(str(cs_id), session)
        except Exception:
            results[str(cs_id)] = 0.0
        if progress and n:
            progress(i + 1, n, str(cs_id))
        time.sleep(delay_sec)
    return results
