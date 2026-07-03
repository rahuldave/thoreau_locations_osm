#!/usr/bin/env python3
"""Cache cautious Nominatim search/reverse results for seeded places."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "fuller_place_seed.json"
CACHE_PATH = ROOT / "data" / "osm_geocode_cache.json"
NOMINATIM = "https://nominatim.openstreetmap.org"
USER_AGENT = "T3Book-Thoreau-location-atlas/0.1 (local research; https://closereading.rahuldave.us)"


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fetch_json(path: str, params: dict[str, str]) -> object:
    url = f"{NOMINATIM}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def search(query: str, limit: int) -> object:
    return fetch_json(
        "/search",
        {
            "format": "jsonv2",
            "q": query,
            "limit": str(limit),
            "addressdetails": "1",
            "extratags": "1",
            "namedetails": "1",
        },
    )


def reverse(lat: float, lon: float) -> object:
    return fetch_json(
        "/reverse",
        {
            "format": "jsonv2",
            "lat": str(lat),
            "lon": str(lon),
            "zoom": "18",
            "addressdetails": "1",
            "extratags": "1",
            "namedetails": "1",
        },
    )


def should_refresh(cache_entry: dict, force: bool) -> bool:
    return force or not cache_entry.get("search_results")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="*", help="Optional place ids to geocode.")
    parser.add_argument("--max", type=int, default=None, help="Maximum uncached places to request.")
    parser.add_argument("--sleep", type=float, default=1.1, help="Delay between Nominatim requests.")
    parser.add_argument("--limit", type=int, default=3, help="Search results per place.")
    parser.add_argument("--force", action="store_true", help="Refresh cached entries.")
    args = parser.parse_args()

    seed = load_json(SEED_PATH, None)
    if seed is None:
        raise SystemExit(f"Missing seed file: {SEED_PATH}")
    cache = load_json(CACHE_PATH, {})
    ids = set(args.ids or [])
    requested = 0

    for place in seed["places"]:
        if ids and place["id"] not in ids:
            continue
        entry = cache.get(place["id"], {})
        if not should_refresh(entry, args.force):
            continue
        if args.max is not None and requested >= args.max:
            break
        print(f"Geocoding {place['id']}: {place['osm_query']}")
        checked_at = datetime.now(timezone.utc).isoformat()
        entry = {
            "place_id": place["id"],
            "query": place["osm_query"],
            "source": "https://nominatim.openstreetmap.org/",
            "checked_at": checked_at,
            "search_results": search(place["osm_query"], args.limit),
        }
        time.sleep(args.sleep)
        if place.get("lat") is not None and place.get("lon") is not None:
            entry["reverse_result"] = reverse(place["lat"], place["lon"])
            time.sleep(args.sleep)
        cache[place["id"]] = entry
        requested += 1

    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {CACHE_PATH}")


if __name__ == "__main__":
    main()
