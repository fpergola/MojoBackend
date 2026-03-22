#!/usr/bin/env python3
"""
Simple client for the Mojo weather backend.

Defaults:
- Seattle-area bbox: -123.40,47.20,-122.00,48.10
- weather types: wind,current
- rows: 80
- cols: 120
- horizon_hours: 6

This script:
1. calls /health
2. calls /latest for wind and current
3. optionally invokes the ingest Lambda if you point it at an AWS API Gateway or custom endpoint
   that supports POST /ingest (disabled by default, since your current Lambda Function URL API
   appears to expose GET endpoints only)

Usage:
    python mojo_backend_client.py --base-url "https://YOUR_FUNCTION_URL/"
    python mojo_backend_client.py --base-url "https://YOUR_FUNCTION_URL/" --type wind
    python mojo_backend_client.py --base-url "https://YOUR_FUNCTION_URL/" --save latest_wind.json

Gamma backend URL: https://pjt6oxdnj2bnqdvsun2pe64eki0lnapf.lambda-url.us-west-2.on.aws/
    

python mojo_backend_client.py \
  --base-url "https://pjt6oxdnj2bnqdvsun2pe64eki0lnapf.lambda-url.us-west-2.on.aws/" \
  --show-params \
  --save seattle_backend_test.json

"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urljoin

import requests


DEFAULT_BBOX = "-123.40,47.20,-122.00,48.10"  # Seattle region
DEFAULT_ROWS = 80
DEFAULT_COLS = 120
DEFAULT_HORIZON_HOURS = 6


def ensure_base_url(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def get_json(url: str, timeout: int = 60) -> Dict[str, Any]:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def post_json(url: str, payload: Dict[str, Any], timeout: int = 300) -> Dict[str, Any]:
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def build_url(base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> str:
    full = urljoin(base_url, path)
    if not params:
        return full
    return f"{full}?{urlencode(params)}"


def print_pretty(title: str, payload: Dict[str, Any]) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, sort_keys=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Prompt the Mojo backend with Seattle defaults.")
    parser.add_argument(
        "--base-url",
        required=True,
        help='Backend base URL, e.g. "https://abc123.lambda-url.us-west-2.on.aws/"',
    )
    parser.add_argument(
        "--type",
        choices=["wind", "current", "both"],
        default="both",
        help="Which forecast type to fetch from /latest.",
    )
    parser.add_argument(
        "--save",
        help="Optional output file for the fetched response(s).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--show-params",
        action="store_true",
        help="Print the default Seattle ingest parameters used by this client.",
    )
    args = parser.parse_args()

    base_url = ensure_base_url(args.base_url)

    seattle_defaults = {
        "weather_types": ["wind", "current"],
        "bbox": DEFAULT_BBOX,
        "rows": DEFAULT_ROWS,
        "cols": DEFAULT_COLS,
        "horizon_hours": DEFAULT_HORIZON_HOURS,
    }

    if args.show_params:
        print_pretty("Seattle default parameters", seattle_defaults)

    try:
        health_url = build_url(base_url, "health")
        health = get_json(health_url, timeout=args.timeout)
        print_pretty("Health", health)

        results: Dict[str, Any] = {"health": health, "defaults": seattle_defaults}

        types = ["wind", "current"] if args.type == "both" else [args.type]
        for forecast_type in types:
            latest_url = build_url(base_url, "latest", {"type": forecast_type})
            latest = get_json(latest_url, timeout=args.timeout)
            print_pretty(f"Latest {forecast_type}", latest)
            results[forecast_type] = latest

        if args.save:
            out_path = Path(args.save)
            out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"\nSaved output to: {out_path}")

        return 0

    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        if exc.response is not None:
            try:
                print(exc.response.text, file=sys.stderr)
            except Exception:
                pass
        return 1
    except requests.RequestException as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())