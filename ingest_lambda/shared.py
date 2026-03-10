import datetime as dt
import json
import math
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_bbox(text: str) -> Tuple[float, float, float, float]:
    min_lon, min_lat, max_lon, max_lat = [float(x.strip()) for x in text.split(',')]
    return min_lon, min_lat, max_lon, max_lat


def isoformat_z(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def hrrr_cycle_candidates(now: Optional[dt.datetime] = None, count: int = 4) -> List[dt.datetime]:
    now = (now or utcnow()).astimezone(dt.timezone.utc)
    base = now.replace(minute=0, second=0, microsecond=0)
    return [base - dt.timedelta(hours=i) for i in range(count)]


def nearest_index(values, target):
    best_i, best_d = None, None
    for i, value in enumerate(values):
        d = abs(value - target)
        if best_d is None or d < best_d:
            best_i, best_d = i, d
    return best_i


def downsample_regular_grid(lat2d, lon2d, u2d, v2d, bbox, rows, cols):
    min_lon, min_lat, max_lon, max_lat = bbox
    out_lats = [min_lat + (max_lat - min_lat) * r / max(rows - 1, 1) for r in range(rows)]
    out_lons = [min_lon + (max_lon - min_lon) * c / max(cols - 1, 1) for c in range(cols)]

    src_lats = [lat2d[r][0] for r in range(len(lat2d))]
    src_lons = [lon2d[0][c] for c in range(len(lon2d[0]))]

    out_u = []
    out_v = []
    for lat in out_lats:
        r_idx = nearest_index(src_lats, lat)
        for lon in out_lons:
            c_idx = nearest_index(src_lons, lon)
            out_u.append(float(u2d[r_idx][c_idx]))
            out_v.append(float(v2d[r_idx][c_idx]))

    return {
        'rows': rows,
        'cols': cols,
        'bbox': {
            'minLat': min_lat,
            'maxLat': max_lat,
            'minLon': min_lon,
            'maxLon': max_lon,
        },
        'u': out_u,
        'v': out_v,
        'units': 'm/s',
        'gridType': 'regular_latlon_resampled_from_hrrr',
    }


def to_field_document(*, weather_type: str, model: str, run_time: str, valid_time: str, field: Dict[str, Any], source: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = {
        'schemaVersion': 1,
        'weatherType': weather_type,
        'model': model,
        'runTime': run_time,
        'validTime': valid_time,
        'field': field,
        'source': source,
        'generatedAt': isoformat_z(utcnow()),
    }
    if metadata:
        doc['metadata'] = metadata
    return doc
