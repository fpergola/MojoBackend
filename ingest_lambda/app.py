import concurrent.futures
import datetime as dt
import io
import json
import os
import re
from typing import Dict, List, Optional, Tuple

import boto3
import numpy as np
import requests
from eccodes import (
    codes_get,
    codes_get_array,
    codes_grib_new_from_file,
    codes_release,
)

from shared import (
    hrrr_cycle_candidates,
    isoformat_z,
    parse_bbox,
    to_field_document,
    downsample_regular_grid,
)

S3 = boto3.client('s3')
OUTPUT_BUCKET = os.environ['OUTPUT_BUCKET']
NOAA_BUCKET = os.environ.get('NOAA_HRRR_BUCKET', 'noaa-hrrr-bdp-pds')
NOAA_REGION = os.environ.get('NOAA_HRRR_REGION', 'us-east-1')
HRRR_DOMAIN = os.environ.get('HRRR_DOMAIN', 'conus')
MODEL_NAME = os.environ.get('MODEL_NAME', 'HRRR')
DEFAULT_BBOX = parse_bbox(os.environ.get('DEFAULT_BBOX', '-123.40,47.20,-122.00,48.10'))
DEFAULT_ROWS = int(os.environ.get('DEFAULT_ROWS', '80'))
DEFAULT_COLS = int(os.environ.get('DEFAULT_COLS', '120'))
DEFAULT_HORIZON_HOURS = int(os.environ.get('DEFAULT_HORIZON_HOURS', '18'))
ENABLE_CURRENT_SCAFFOLD = os.environ.get('ENABLE_CURRENT_SCAFFOLD', 'true').lower() == 'true'

session = requests.Session()


class HrrrSourceNotFound(Exception):
    pass


def s3_https_url(key: str) -> str:
    return f'https://{NOAA_BUCKET}.s3.{NOAA_REGION}.amazonaws.com/{key}'


def hrrr_key(run: dt.datetime, forecast_hour: int) -> str:
    ymd = run.strftime('%Y%m%d')
    cyc = run.strftime('%H')
    return f'hrrr.{ymd}/{HRRR_DOMAIN}/hrrr.t{cyc}z.wrfsfcf{forecast_hour:02d}.grib2'


def fetch_index_lines(url: str) -> List[str]:
    idx_url = url + '.idx'
    resp = session.get(idx_url, timeout=60)
    if resp.status_code == 404:
        raise HrrrSourceNotFound(idx_url)
    resp.raise_for_status()
    return [line for line in resp.text.splitlines() if line.strip()]


def parse_index_for_ranges(lines: List[str], patterns: List[str]) -> List[Tuple[int, Optional[int], str]]:
    out = []
    compiled = [re.compile(p) for p in patterns]
    starts = []
    records = []
    for line in lines:
        parts = line.split(':')
        if len(parts) < 5:
            continue
        start = int(parts[1])
        descr = ':'.join(parts[3:])
        starts.append(start)
        records.append((start, descr))
    for i, (start, descr) in enumerate(records):
        if any(p.search(descr) for p in compiled):
            end = records[i + 1][0] - 1 if i + 1 < len(records) else None
            out.append((start, end, descr))
    return out


def fetch_ranges(url: str, ranges: List[Tuple[int, Optional[int], str]]) -> bytes:
    chunks = []
    for start, end, _ in ranges:
        hdr = {'Range': f'bytes={start}-{end}' if end is not None else f'bytes={start}-'}
        resp = session.get(url, headers=hdr, timeout=120)
        resp.raise_for_status()
        chunks.append(resp.content)
    return b''.join(chunks)


def decode_grib_messages(blob: bytes) -> Dict[str, Dict[str, np.ndarray]]:
    out = {}
    fh = io.BytesIO(blob)
    while True:
        gid = codes_grib_new_from_file(fh)
        if gid is None:
            break
        try:
            short_name = codes_get(gid, 'shortName')
            level_desc = f"{codes_get(gid, 'level')} {codes_get(gid, 'typeOfLevel')}"
            ni = codes_get(gid, 'Ni')
            nj = codes_get(gid, 'Nj')
            values = np.array(codes_get_array(gid, 'values'), dtype=np.float32).reshape(nj, ni)
            lats = np.array(codes_get_array(gid, 'latitudes'), dtype=np.float32).reshape(nj, ni)
            lons = np.array(codes_get_array(gid, 'longitudes'), dtype=np.float32).reshape(nj, ni)
            out[short_name] = {
                'values': values,
                'lats': lats,
                'lons': lons,
                'level': level_desc,
            }
        finally:
            codes_release(gid)
    return out


def choose_run_and_url(forecast_hour: int) -> Tuple[dt.datetime, str, str]:
    for run in hrrr_cycle_candidates(count=6):
        key = hrrr_key(run, forecast_hour)
        url = s3_https_url(key)
        try:
            fetch_index_lines(url)
            return run, key, url
        except HrrrSourceNotFound:
            continue
    raise HrrrSourceNotFound(f'No HRRR cycle found for f{forecast_hour:02d}')


def build_wind_document(run: dt.datetime, forecast_hour: int, bbox, rows: int, cols: int) -> Dict:
    run2, key, url = choose_run_and_url(forecast_hour)
    idx_lines = fetch_index_lines(url)
    ranges = parse_index_for_ranges(
        idx_lines,
        patterns=[r'UGRD:10 m above ground', r'VGRD:10 m above ground'],
    )
    if len(ranges) < 2:
        raise RuntimeError(f'Could not find UGRD/VGRD 10m in index for {url}')
    blob = fetch_ranges(url, ranges)
    decoded = decode_grib_messages(blob)
    ugrd = decoded.get('10u') or decoded.get('u') or decoded.get('ugrd')
    vgrd = decoded.get('10v') or decoded.get('v') or decoded.get('vgrd')
    if not ugrd or not vgrd:
        # ecCodes short names vary; fall back to first two decoded messages when needed.
        keys = list(decoded.keys())
        if len(keys) >= 2:
            ugrd = decoded[keys[0]]
            vgrd = decoded[keys[1]]
        else:
            raise RuntimeError(f'Failed to decode wind messages from {url}; keys={keys}')

    field = downsample_regular_grid(
        ugrd['lats'].tolist(),
        ugrd['lons'].tolist(),
        ugrd['values'].tolist(),
        vgrd['values'].tolist(),
        bbox,
        rows,
        cols,
    )
    valid_time = run2 + dt.timedelta(hours=forecast_hour)
    return to_field_document(
        weather_type='wind',
        model=MODEL_NAME,
        run_time=isoformat_z(run2),
        valid_time=isoformat_z(valid_time),
        field=field,
        source={
            'provider': 'NOAA AWS Open Data',
            'bucket': NOAA_BUCKET,
            'key': key,
            'domain': HRRR_DOMAIN,
            'product': 'wrfsfc',
            'variables': ['UGRD', 'VGRD'],
            'level': '10 m above ground',
        },
        metadata={
            'forecastHour': forecast_hour,
            'notes': 'Nearest-neighbor resample from HRRR native grid to app regular grid. Replace with bilinear or vector-aware resample if desired.'
        }
    )


def build_current_placeholder(run: dt.datetime, forecast_hour: int, bbox, rows: int, cols: int) -> Dict:
    min_lon, min_lat, max_lon, max_lat = bbox
    count = rows * cols
    field = {
        'rows': rows,
        'cols': cols,
        'bbox': {
            'minLat': min_lat,
            'maxLat': max_lat,
            'minLon': min_lon,
            'maxLon': max_lon,
        },
        'u': [0.0] * count,
        'v': [0.0] * count,
        'units': 'm/s',
        'gridType': 'regular_latlon_placeholder',
    }
    valid_time = run + dt.timedelta(hours=forecast_hour)
    return to_field_document(
        weather_type='current',
        model='PLACEHOLDER_CURRENT',
        run_time=isoformat_z(run),
        valid_time=isoformat_z(valid_time),
        field=field,
        source={
            'provider': 'placeholder',
            'notes': 'Replace with real current source such as NOAA OFS, HYCOM, CMEMS, or local tide/current nowcast pipeline.'
        },
        metadata={
            'forecastHour': forecast_hour,
            'missing': ['data source', 'API contract', 'coastline mask', 'vector decoder']
        }
    )


def write_doc(weather_type: str, valid_time: str, doc: Dict):
    key = f'processed/{weather_type}/{valid_time}.json'
    S3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=key,
        Body=json.dumps(doc).encode('utf-8'),
        ContentType='application/json',
    )
    latest_key = f'processed/{weather_type}/latest.json'
    S3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=latest_key,
        Body=json.dumps({'latest': key, 'validTime': valid_time}).encode('utf-8'),
        ContentType='application/json',
    )
    return key


def handler(event, context):
    bbox = parse_bbox(event.get('bbox') or os.environ.get('DEFAULT_BBOX', '-123.40,47.20,-122.00,48.10'))
    rows = int(event.get('rows') or DEFAULT_ROWS)
    cols = int(event.get('cols') or DEFAULT_COLS)
    horizon_hours = int(event.get('horizon_hours') or DEFAULT_HORIZON_HOURS)
    weather_types = event.get('weather_types') or ['wind']

    run_base = hrrr_cycle_candidates(count=1)[0]
    results = []
    errors = []

    for fxx in range(horizon_hours + 1):
        if 'wind' in weather_types:
            try:
                doc = build_wind_document(run_base, fxx, bbox, rows, cols)
                results.append({'type': 'wind', 'key': write_doc('wind', doc['validTime'], doc)})
            except Exception as exc:
                errors.append({'type': 'wind', 'forecastHour': fxx, 'error': str(exc)})
        if 'current' in weather_types and ENABLE_CURRENT_SCAFFOLD:
            try:
                doc = build_current_placeholder(run_base, fxx, bbox, rows, cols)
                results.append({'type': 'current', 'key': write_doc('current', doc['validTime'], doc)})
            except Exception as exc:
                errors.append({'type': 'current', 'forecastHour': fxx, 'error': str(exc)})

    return {
        'ok': len(errors) == 0,
        'bucket': OUTPUT_BUCKET,
        'results': results,
        'errors': errors,
    }
