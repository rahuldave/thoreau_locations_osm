#!/usr/bin/env python3
"""Build chronology-to-location link rules from the atlas catalog."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
CATALOG_PATH = ROOT / "data" / "fuller_place_catalog.json"
SUPPRESSIONS_PATH = ROOT / "data" / "chronology_location_suppressions.json"
OVERRIDES_PATH = ROOT / "data" / "chronology_location_overrides.json"
OUTPUT_PATH = ROOT / "data" / "chronology_location_links.json"
CHRONOLOGY_OUTPUT_PATH = WORKSPACE / "thoreau_biographies_chronology" / "location_anchor_rules.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def link_for(place: dict, source: str) -> dict:
    return {
        "place_id": place["id"],
        "label": place["canonical_name"],
        "href": place["public_atlas_url"],
        "source": source,
    }


def build_rules(catalog: dict, suppressions: dict, overrides: dict) -> dict:
    places = {place["id"]: place for place in catalog["places"]}
    suppressed = {
        (item["public_event_id"], item["place_id"])
        for item in suppressions.get("suppressions", [])
    }
    events: dict[str, dict[str, dict]] = defaultdict(dict)

    for place in catalog["places"]:
        for event in place.get("chronology_events", []):
            public_event_id = event.get("public_event_id")
            if not public_event_id:
                continue
            if (public_event_id, place["id"]) in suppressed:
                continue
            events[public_event_id][place["id"]] = link_for(place, "auto")

    for item in overrides.get("overrides", []):
        place_id = item["place_id"]
        public_event_id = item["public_event_id"]
        if place_id not in places:
            raise KeyError(f"Unknown place_id in override: {place_id}")
        link = link_for(places[place_id], "override")
        if item.get("label"):
            link["label"] = item["label"]
        if item.get("href"):
            link["href"] = item["href"]
        if item.get("note"):
            link["note"] = item["note"]
        events[public_event_id][place_id] = link

    return {
        "schema_version": 1,
        "generated_from": "thoreau_locations_osm/data/fuller_place_catalog.json",
        "atlas_base_url": "https://rahuldave.com/thoreau_locations_osm/fuller_location_atlas.html",
        "counts": {
            "events": len(events),
            "links": sum(len(links) for links in events.values()),
            "suppressions": len(suppressed),
            "overrides": len(overrides.get("overrides", [])),
        },
        "events": {
            event_id: sorted(links.values(), key=lambda link: link["label"].lower())
            for event_id, links in sorted(events.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--suppressions", type=Path, default=SUPPRESSIONS_PATH)
    parser.add_argument("--overrides", type=Path, default=OVERRIDES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--chronology-output", type=Path, default=CHRONOLOGY_OUTPUT_PATH)
    args = parser.parse_args()

    rules = build_rules(
        load_json(args.catalog),
        load_json(args.suppressions),
        load_json(args.overrides),
    )
    text = json.dumps(rules, indent=2, ensure_ascii=False) + "\n"
    args.output.write_text(text, encoding="utf-8")
    if args.chronology_output:
        args.chronology_output.write_text(text, encoding="utf-8")
    print(f"Wrote {args.output}")
    if args.chronology_output:
        print(f"Wrote {args.chronology_output}")


if __name__ == "__main__":
    main()
