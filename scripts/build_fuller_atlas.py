#!/usr/bin/env python3
"""Build the Fuller place-review atlas from the close-reading database."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
SEED_PATH = ROOT / "data" / "fuller_place_seed.json"
GEOCODE_CACHE_PATH = ROOT / "data" / "osm_geocode_cache.json"
BOOK_LINE_ANCHORS_PATH = WORKSPACE / "thoreau_biographies_chronology" / "book_line_anchors.json"
GEOREFERENCE_CONTROL_POINTS_PATH = ROOT / "data" / "georeference_control_points.json"
VISITABILITY_SOURCES_PATH = ROOT / "data" / "visitability_sources.json"
CATALOG_PATH = ROOT / "data" / "fuller_place_catalog.json"
MENTIONS_CSV_PATH = ROOT / "data" / "fuller_place_mentions.csv"
EVENTS_CSV_PATH = ROOT / "data" / "fuller_place_chronology_events.csv"
HTML_PATH = ROOT / "fuller_location_atlas.html"
OLD_MAP_HTML_PATH = ROOT / "old_map_georeference.html"
INDEX_PATH = ROOT / "index.html"
CLOSE_READING_CHAPTER_BASE = "https://closereading.rahuldave.us/books/{book_id}/chapters/{chapter_id}#cell-{cell_index}"
PUBLIC_CHRONOLOGY_BASE = "https://rahuldave.com/thoreau_biographies_chronology/aligned_chronologies.html"
PUBLIC_ATLAS_BASE = "https://rahuldave.com/thoreau_locations_osm/fuller_location_atlas.html"

CHRONOLOGY_EVENT_FILES = [
    "thoreau_chronology.md",
    "sanborn_chronology.md",
    "brace_chronology.md",
    "bronson_alcott_chronology.md",
    "emerson_chronology.md",
]


IMPORTANCE_ORDER = {"primary": 0, "secondary": 1, "tertiary": 2}
STATUS_ORDER = {
    "needs_historical_map": 0,
    "needs_resolution": 1,
    "needs_confirmation": 2,
    "seeded": 3,
}
EXCLUDED_CATALOG_KINDS = {
    "capital-city",
    "canal",
    "country",
    "country-or-refuge-context",
    "county",
    "historical-region",
    "islands",
    "region",
    "river",
    "road",
    "settlement",
    "settlement-and-historic-site",
    "settlement-or-field-site",
    "settlement-or-institution",
    "settlement-or-speech-context",
    "state",
}
VISITABLE_STATUSES = {
    "active_campus_site",
    "active_church_or_successor_site",
    "active_library_successor_site",
    "active_municipal_building",
    "house_museum",
    "museum_site",
    "operating_hotel_successor",
    "public_historic_cemetery",
    "public_historic_park",
    "public_state_reservation",
    "public_state_reservation_marker",
    "seasonal_house_museum",
    "seasonal_nps_site",
    "site_on_museum_grounds",
    "state_historic_site",
}
CHRONOLOGY_PUBLIC_IDS = {
    "thoreau_chronology": "thoreau",
    "sanborn_chronology": "sanborn",
    "brace_chronology": "brace",
    "bronson_alcott_chronology": "alcott",
    "emerson_chronology": "emerson",
}


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def alias_pattern(aliases: list[str]) -> re.Pattern[str]:
    parts = []
    for alias in sorted(set(aliases), key=len, reverse=True):
        escaped = re.escape(alias)
        parts.append(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])")
    return re.compile("|".join(parts), re.IGNORECASE)


def snippet_for(text: str, match: re.Match[str], width: int = 280) -> str:
    flat = collapse(text)
    matched = collapse(match.group(0))
    idx = flat.lower().find(matched.lower())
    if idx < 0:
        return flat[: width - 1] + ("..." if len(flat) >= width else "")
    start = max(0, idx - width // 3)
    end = min(len(flat), idx + len(matched) + width * 2 // 3)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(flat) else ""
    return f"{prefix}{flat[start:end]}{suffix}"


def read_cells(db_path: Path, book_id: int) -> list[sqlite3.Row]:
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        return list(
            con.execute(
                """
                select
                  ch.chapter_index,
                  ch.id as chapter_id,
                  ch.title as chapter_title,
                  c.cell_index,
                  c.cell_type,
                  c.markdown
                from chapters ch
                join cells c on c.chapter_id = ch.id
                where ch.book_id = ?
                order by ch.chapter_index, c.cell_index
                """,
                (book_id,),
            )
        )
    finally:
        con.close()


def cell_mentions(place: dict, cells: list[sqlite3.Row], book_id: int) -> tuple[int, list[dict]]:
    pattern = alias_pattern(place["aliases"])
    count = 0
    examples = []
    seen_cells = set()
    for row in cells:
        text = row["markdown"]
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        count += len(matches)
        cell_key = (row["chapter_id"], row["cell_index"])
        if cell_key in seen_cells:
            continue
        seen_cells.add(cell_key)
        match_aliases = sorted({collapse(match.group(0)) for match in matches})
        examples.append(
            {
                "chapter_index": row["chapter_index"],
                "chapter_id": row["chapter_id"],
                "chapter_title": row["chapter_title"],
                "cell_index": row["cell_index"],
                "cell_type": row["cell_type"],
                "matched_aliases": match_aliases,
                "snippet": snippet_for(text, matches[0]),
                "close_reading_url": CLOSE_READING_CHAPTER_BASE.format(
                    book_id=book_id,
                    chapter_id=row["chapter_id"],
                    cell_index=row["cell_index"],
                ),
            }
        )
    return count, examples


def chronology_mentions(place: dict, chronology_path: Path) -> tuple[int, list[dict]]:
    if not chronology_path.exists():
        return 0, []
    pattern = alias_pattern(place["aliases"])
    count = 0
    examples = []
    for line_number, line in enumerate(chronology_path.read_text(encoding="utf-8").splitlines(), start=1):
        matches = list(pattern.finditer(line))
        if not matches:
            continue
        count += len(matches)
        if len(examples) < 12:
            examples.append(
                {
                    "line": line_number,
                    "matched_aliases": sorted({collapse(match.group(0)) for match in matches}),
                    "snippet": collapse(line),
                    "local_path": str(chronology_path.relative_to(WORKSPACE)),
                }
            )
    return count, examples


def source_key_map(markdown: str) -> dict[str, dict]:
    sources = {}
    for line in markdown.splitlines():
        match = re.match(r"^- \[([^\]]+)\]\((<[^>]+>|[^)]+)\)\s+-\s+(.+)$", line)
        if not match:
            continue
        sources[match.group(1)] = {
            "url": match.group(2).strip("<>"),
            "label": match.group(1),
            "description": match.group(3).strip(),
        }
    return sources


def chronology_label(path: Path) -> str:
    return path.stem.replace("_chronology", "").replace("_", " ").title()


def parse_source_keys(text: str) -> list[str]:
    keys = []
    for label in ("Sources", "Web"):
        for match in re.finditer(rf"{label}:\s*([^.\n]+)", text):
            for raw in re.split(r"[,;]\s*", match.group(1)):
                key = raw.strip()
                if key and re.match(r"^[A-Za-z0-9-]+$", key):
                    keys.append(key)
    return sorted(set(keys))


def parse_book_anchor_refs(text: str, anchors: dict) -> list[dict]:
    refs = []
    for line_range in sorted(set(re.findall(r"book\.md:(\d+-\d+)", text))):
        anchor = anchors.get(line_range)
        refs.append(
            {
                "line_range": line_range,
                "start": anchor.get("start") if anchor else None,
                "end": anchor.get("end") if anchor else None,
                "mapped": bool(anchor),
            }
        )
    return refs


def parse_chronology_events(source_book: dict) -> list[dict]:
    chronology_dir = WORKSPACE / "thoreau_biographies_chronology"
    main_chronology = (ROOT / source_book["chronology_path"]).resolve()
    source_keys = source_key_map(main_chronology.read_text(encoding="utf-8")) if main_chronology.exists() else {}
    book_anchors = load_json(BOOK_LINE_ANCHORS_PATH, {})
    events = []
    for filename in CHRONOLOGY_EVENT_FILES:
        path = chronology_dir / filename
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        index = 0
        event_order = 0
        while index < len(lines):
            line = lines[index]
            match = re.match(r"^- \*\*(.+?)\*\*(.*)$", line)
            if not match:
                index += 1
                continue
            start_line = index + 1
            block = [line]
            index += 1
            while index < len(lines) and not re.match(r"^- \*\*", lines[index]):
                if lines[index].startswith("## ") and block:
                    break
                block.append(lines[index])
                index += 1
            block_text = "\n".join(block)
            collapsed = collapse(block_text)
            source_key_ids = parse_source_keys(collapsed)
            event_order += 1
            public_event_id = None
            public_chronology_id = CHRONOLOGY_PUBLIC_IDS.get(path.stem)
            if public_chronology_id:
                public_event_id = f"{public_chronology_id}-{event_order}"
            events.append(
                {
                    "event_id": f"{path.stem}:{start_line}",
                    "public_event_id": public_event_id,
                    "public_chronology_url": (
                        f"{PUBLIC_CHRONOLOGY_BASE}#event-{public_event_id}" if public_event_id else PUBLIC_CHRONOLOGY_BASE
                    ),
                    "chronology": chronology_label(path),
                    "local_path": str(path.relative_to(WORKSPACE)),
                    "line": start_line,
                    "headline": collapse(re.sub(r"\*\*", "", line).lstrip("- ")),
                    "summary": next(
                        (
                            collapse(summary_match.group(1))
                            for summary_match in [re.search(r"- Summary:\s*(.+)", block_text)]
                            if summary_match
                        ),
                        "",
                    ),
                    "book_anchors": parse_book_anchor_refs(block_text, book_anchors),
                    "source_keys": [
                        {
                            "key": key,
                            "url": source_keys.get(key, {}).get("url"),
                            "description": source_keys.get(key, {}).get("description"),
                        }
                        for key in source_key_ids
                    ],
                    "web_context_lines": [
                        collapse(part)
                        for part in re.findall(r"- Web context:\s*(.+)", block_text)
                    ],
                    "text": collapsed,
                }
            )
    return events


def matched_chronology_events(place: dict, events: list[dict]) -> list[dict]:
    pattern = alias_pattern(place["aliases"])
    matches = []
    for event in events:
        found = sorted({collapse(match.group(0)) for match in pattern.finditer(event["text"])})
        if not found:
            continue
        matches.append(
            {
                "event_id": event["event_id"],
                "public_event_id": event["public_event_id"],
                "public_chronology_url": event["public_chronology_url"],
                "chronology": event["chronology"],
                "local_path": event["local_path"],
                "line": event["line"],
                "headline": event["headline"],
                "summary": event["summary"],
                "matched_aliases": found,
                "book_anchors": event["book_anchors"],
                "source_keys": event["source_keys"],
                "web_context_lines": event["web_context_lines"],
            }
        )
    return matches


def merge_visitability(place: dict, visitability_data: dict) -> dict:
    defaults = visitability_data.get("defaults", {})
    place_visitability = visitability_data.get("places", {}).get(place["id"], {})
    merged = {**defaults, **place_visitability}
    merged["sources"] = merged.get("sources", [])
    return merged


def merge_web_refs(place: dict, visitability: dict) -> list[dict]:
    refs = []
    seen_urls = set()
    for ref in [*place.get("web_refs", []), *visitability.get("sources", [])]:
        url = ref.get("url")
        if not url or url in seen_urls:
            continue
        refs.append(ref)
        seen_urls.add(url)
    return refs


def current_address(place: dict, osm_result: dict | None) -> str | None:
    if place.get("current_address"):
        return place["current_address"]
    if osm_result and osm_result.get("display_name"):
        return osm_result["display_name"]
    query = place.get("osm_query", "")
    if re.match(r"^\d+\b", query):
        return query
    return None


def google_maps_url(place: dict, osm_result: dict | None, address: str | None) -> str:
    lat = place.get("lat") or (osm_result or {}).get("lat")
    lon = place.get("lon") or (osm_result or {}).get("lon")
    if lat and lon:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(f'{lat},{lon}')}"
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(address or place['osm_query'])}"


def public_atlas_url(place: dict) -> str:
    return f"{PUBLIC_ATLAS_BASE}?place={quote_plus(place['id'])}"


def is_catalog_place(place: dict) -> bool:
    if place.get("catalog_scope") == "context_only":
        return False
    return place.get("kind") not in EXCLUDED_CATALOG_KINDS


def best_osm_result(cache_entry: dict | None) -> tuple[str | None, dict | None]:
    if not cache_entry:
        return None, None
    for key in ("search_results", "reverse_result"):
        value = cache_entry.get(key)
        if isinstance(value, list) and value:
            return key, value[0]
        if isinstance(value, dict) and value:
            return key, value
    return None, None


def osm_url(result: dict | None) -> str | None:
    if not result:
        return None
    osm_type = result.get("osm_type")
    osm_id = result.get("osm_id")
    if not osm_type or not osm_id:
        return None
    osm_path = {"node": "node", "way": "way", "relation": "relation"}.get(str(osm_type).lower())
    if not osm_path:
        return None
    return f"https://www.openstreetmap.org/{osm_path}/{osm_id}"


def enrich(seed: dict, max_examples: int) -> list[dict]:
    source_book = seed["source_book"]
    db_path = (ROOT / source_book["database_path"]).resolve()
    chronology_path = (ROOT / source_book["chronology_path"]).resolve()
    book_id = int(source_book["close_reading_book_id"])
    cells = read_cells(db_path, book_id)
    geocode_cache = load_json(GEOCODE_CACHE_PATH, {})
    visitability_data = load_json(VISITABILITY_SOURCES_PATH, {"defaults": {}, "places": {}})
    chronology_events = parse_chronology_events(source_book)
    places = []

    for place in seed["places"]:
        if not is_catalog_place(place):
            continue
        visitability = merge_visitability(place, visitability_data)
        mention_count, examples = cell_mentions(place, cells, book_id)
        chronology_count, chronology_examples = chronology_mentions(place, chronology_path)
        event_matches = matched_chronology_events(place, chronology_events)
        cache_entry = geocode_cache.get(place["id"], {})
        match_method, result = best_osm_result(cache_entry)
        candidate = None
        if result:
            candidate = {
                "display_name": result.get("display_name"),
                "osm_type": result.get("osm_type"),
                "osm_id": result.get("osm_id"),
                "category": result.get("category") or result.get("class"),
                "type": result.get("type"),
                "lat": result.get("lat"),
                "lon": result.get("lon"),
                "url": osm_url(result),
                "match_method": "forward_search" if match_method == "search_results" else "reverse_nearest",
                "review_status": place.get(
                    "osm_review_status",
                    "accepted" if place.get("review_status") == "seeded" and match_method == "search_results" else "candidate",
                ),
                "review_note": place.get("osm_review_note"),
                "source": cache_entry.get("source"),
                "checked_at": cache_entry.get("checked_at"),
            }
        address = current_address(place, result)
        places.append(
            {
                **place,
                "current_address": address,
                "google_maps_url": google_maps_url(place, result, address),
                "public_atlas_url": public_atlas_url(place),
                "book_mention_count": mention_count,
                "book_citation_count": len(examples),
                "chronology_mention_count": chronology_count,
                "chronology_event_count": len(event_matches),
                "book_mentions": examples,
                "book_mentions_preview": examples[:max_examples],
                "chronology_mentions": chronology_examples,
                "chronology_mentions_preview": chronology_examples[:max_examples],
                "chronology_events": event_matches,
                "chronology_events_preview": event_matches[:max_examples],
                "osm_candidate": candidate,
                "osm_search_url": f"https://www.openstreetmap.org/search?query={quote_plus(place['osm_query'])}",
                "web_search_url": f"https://www.google.com/search?q={quote_plus(place['canonical_name'])}",
                "web_refs": merge_web_refs(place, visitability),
                "web_confirmation_count": len(merge_web_refs(place, visitability)),
                "visitability": visitability,
            }
        )

    return sorted(
        places,
        key=lambda item: (
            IMPORTANCE_ORDER.get(item.get("importance"), 9),
            STATUS_ORDER.get(item.get("review_status"), 9),
            -item["book_mention_count"],
            item["canonical_name"].lower(),
        ),
    )


def write_catalog(seed: dict, places: list[dict]) -> None:
    georeference = load_json(GEOREFERENCE_CONTROL_POINTS_PATH, {})
    suppressed_context_places = [
        {"id": place["id"], "canonical_name": place["canonical_name"], "kind": place["kind"]}
        for place in seed["places"]
        if not is_catalog_place(place)
    ]
    output = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_book": seed["source_book"],
        "georeference": georeference,
        "suppressed_context_places": suppressed_context_places,
        "counts": {
            "places": len(places),
            "suppressed_context_places": len(suppressed_context_places),
            "places_with_book_mentions": sum(1 for place in places if place["book_mention_count"]),
            "book_citations": sum(place["book_citation_count"] for place in places),
            "chronology_events": sum(place["chronology_event_count"] for place in places),
            "places_with_web_confirmations": sum(1 for place in places if place["web_confirmation_count"]),
            "places_with_current_addresses": sum(1 for place in places if place.get("current_address")),
            "visitable_places": sum(
                1 for place in places if place["visitability"].get("visit_status") in VISITABLE_STATUSES
            ),
            "places_with_visit_research": sum(
                1 for place in places if place["visitability"].get("visit_status") != "needs_visit_research"
            ),
            "private_residence_sites": sum(
                1 for place in places if place["visitability"].get("visit_status") == "private_residence"
            ),
            "places_needing_historical_map": sum(
                1 for place in places if place["review_status"] == "needs_historical_map"
            ),
            "places_with_osm_candidates": sum(1 for place in places if place.get("osm_candidate")),
        },
        "places": places,
    }
    CATALOG_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_mentions_csv(places: list[dict]) -> None:
    with MENTIONS_CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "place_id",
                "canonical_name",
                "chapter_index",
                "chapter_title",
                "cell_index",
                "matched_aliases",
                "close_reading_url",
                "snippet",
            ],
        )
        writer.writeheader()
        for place in places:
            for mention in place["book_mentions"]:
                writer.writerow(
                    {
                        "place_id": place["id"],
                        "canonical_name": place["canonical_name"],
                        "chapter_index": mention["chapter_index"],
                        "chapter_title": mention["chapter_title"],
                        "cell_index": mention["cell_index"],
                        "matched_aliases": "; ".join(mention["matched_aliases"]),
                        "close_reading_url": mention["close_reading_url"],
                        "snippet": mention["snippet"],
                    }
                )


def write_events_csv(places: list[dict]) -> None:
    with EVENTS_CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "place_id",
                "canonical_name",
                "event_id",
                "public_event_id",
                "public_chronology_url",
                "chronology",
                "local_path",
                "line",
                "matched_aliases",
                "headline",
                "book_anchor_ranges",
                "book_anchor_urls",
                "source_keys",
                "source_urls",
            ],
        )
        writer.writeheader()
        for place in places:
            for event in place["chronology_events"]:
                book_urls = []
                for anchor in event["book_anchors"]:
                    for endpoint in ("start", "end"):
                        value = anchor.get(endpoint)
                        if value and value.get("href"):
                            book_urls.append(value["href"])
                writer.writerow(
                    {
                        "place_id": place["id"],
                        "canonical_name": place["canonical_name"],
                        "event_id": event["event_id"],
                        "public_event_id": event["public_event_id"],
                        "public_chronology_url": event["public_chronology_url"],
                        "chronology": event["chronology"],
                        "local_path": event["local_path"],
                        "line": event["line"],
                        "matched_aliases": "; ".join(event["matched_aliases"]),
                        "headline": event["headline"],
                        "book_anchor_ranges": "; ".join(anchor["line_range"] for anchor in event["book_anchors"]),
                        "book_anchor_urls": "; ".join(sorted(set(book_urls))),
                        "source_keys": "; ".join(source["key"] for source in event["source_keys"]),
                        "source_urls": "; ".join(
                            sorted({source["url"] for source in event["source_keys"] if source.get("url")})
                        ),
                    }
                )


def html_escape_json(data: dict) -> str:
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def write_html(catalog: dict) -> None:
    payload = html_escape_json(catalog)
    HTML_PATH.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fuller Location Atlas</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    :root {{
      color-scheme: light;
      --ink: #202124;
      --muted: #5b626a;
      --line: #d9ded7;
      --paper: #fbfaf6;
      --panel: #ffffff;
      --green: #2f6f5e;
      --blue: #315f8f;
      --rust: #a04d37;
      --gold: #a97822;
      --wash: #edf3f1;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--paper);
    }}
    header {{
      display: flex;
      gap: 24px;
      align-items: end;
      justify-content: space-between;
      padding: 20px 24px 16px;
      border-bottom: 1px solid var(--line);
      background: #f6f2ea;
    }}
    h1 {{
      margin: 0 0 4px;
      font-size: 24px;
      letter-spacing: 0;
    }}
    .subtle {{ color: var(--muted); }}
    .stats {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .stat {{
      min-width: 108px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 6px;
    }}
    .stat strong {{
      display: block;
      font-size: 18px;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(340px, 430px) 1fr;
      min-height: calc(100vh - 91px);
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: var(--panel);
      min-width: 0;
    }}
    .filters {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 14px;
      border-bottom: 1px solid var(--line);
      background: #faf8f2;
    }}
    .filters input,
    .filters select {{
      width: 100%;
      height: 34px;
      border: 1px solid #cbd2cd;
      border-radius: 6px;
      padding: 0 10px;
      background: white;
      color: var(--ink);
    }}
    .filters input {{ grid-column: 1 / -1; }}
    .place-list {{
      max-height: calc(100vh - 170px);
      overflow: auto;
    }}
    .place-row {{
      width: 100%;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: white;
      text-align: left;
      padding: 12px 14px;
      cursor: pointer;
    }}
    .place-row:hover,
    .place-row.active {{ background: var(--wash); }}
    .place-row strong {{
      display: block;
      font-size: 14px;
      margin-bottom: 4px;
    }}
    .chips {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-top: 6px;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 7px;
      border-radius: 999px;
      border: 1px solid #c8d2cf;
      background: #f6fbf9;
      color: #294d45;
      font-size: 12px;
      white-space: nowrap;
    }}
    .chip.need {{ border-color: #e0b7a9; background: #fff4ef; color: #78341f; }}
    .chip.map {{ border-color: #d7bf7b; background: #fff9df; color: #6b4e0b; }}
    .chip.osm {{ border-color: #bdd1e5; background: #f1f7ff; color: #244b73; }}
    .chip.visit {{ border-color: #b9d6c5; background: #f1fbf4; color: #24533a; }}
    .chip.private {{ border-color: #e4b29f; background: #fff3ed; color: #79391f; }}
    .chip.source {{ border-color: #c5c2df; background: #f5f4ff; color: #3c3970; }}
    .map-legend {{
      position: absolute;
      z-index: 500;
      right: 12px;
      bottom: 16px;
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
      color: var(--muted);
      font-size: 12px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
    }}
    .legend-dot {{
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 999px;
      margin-right: 5px;
      background: #d12f7a;
      border: 2px solid #4a1430;
      vertical-align: -1px;
    }}
    .content {{
      display: grid;
      grid-template-rows: minmax(340px, 46vh) 1fr;
      min-width: 0;
    }}
    #map {{ position: relative; min-height: 340px; border-bottom: 1px solid var(--line); }}
    .details {{
      padding: 18px 22px 30px;
      overflow: auto;
      background: #fffdfa;
    }}
    .details h2 {{
      margin: 0 0 8px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    .actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 12px 0 16px;
    }}
    a.action {{
      color: white;
      background: var(--blue);
      border-radius: 6px;
      padding: 7px 10px;
      text-decoration: none;
      font-weight: 650;
    }}
    a.action.secondary {{ background: var(--green); }}
    a.action.pending {{ background: var(--rust); }}
    section {{
      border-top: 1px solid var(--line);
      padding-top: 14px;
      margin-top: 14px;
    }}
    h3 {{
      margin: 0 0 8px;
      font-size: 15px;
      letter-spacing: 0;
    }}
    .mention {{
      border-left: 3px solid #b7c9d8;
      padding: 8px 10px;
      margin: 8px 0;
      background: #f7fafc;
      border-radius: 0 6px 6px 0;
    }}
    .mention a {{
      display: inline-block;
      margin-bottom: 3px;
      color: var(--blue);
      font-weight: 650;
      text-decoration: none;
    }}
    code {{
      background: #f0eee8;
      border: 1px solid #e2ddd1;
      border-radius: 4px;
      padding: 1px 4px;
    }}
    @media (max-width: 860px) {{
      header {{ display: block; }}
      .stats {{ justify-content: flex-start; margin-top: 12px; }}
      main {{ grid-template-columns: 1fr; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      .place-list {{ max-height: 280px; }}
      .content {{ grid-template-rows: 320px auto; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Fuller Location Atlas</h1>
      <div class="subtle">The Book That Changed America - addressable site review queue for OSM, web, visit, and old-map confirmation</div>
    </div>
    <div class="stats" id="stats"></div>
  </header>
  <main>
    <aside>
      <div class="filters">
        <input id="search" type="search" placeholder="Search places, aliases, notes">
        <select id="status">
          <option value="">All review states</option>
        </select>
        <select id="importance">
          <option value="">All priorities</option>
        </select>
        <select id="visit">
          <option value="">All visitability</option>
          <option value="visitable">Visitable / public-ish</option>
          <option value="private_residence">Private residences</option>
          <option value="needs_visit_research">Needs visit research</option>
        </select>
      </div>
      <div class="place-list" id="place-list"></div>
    </aside>
    <div class="content">
      <div id="map"></div>
      <div class="details" id="details"></div>
    </div>
  </main>
  <script id="catalog-data" type="application/json">{payload}</script>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const catalog = JSON.parse(document.getElementById("catalog-data").textContent);
    const places = catalog.places;
    const initialPlaceId = new URLSearchParams(window.location.search).get("place");
    const state = {{
      selectedId: places.some((place) => place.id === initialPlaceId) ? initialPlaceId : places[0]?.id || null
    }};
    const map = L.map("map", {{ scrollWheelZoom: true }}).setView([42.455, -71.35], 11);
    L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }}).addTo(map);
    const legend = L.control({{ position: "bottomright" }});
    legend.onAdd = () => {{
      const div = L.DomUtil.create("div", "map-legend");
      div.innerHTML = '<span class="legend-dot"></span>current place';
      return div;
    }};
    legend.addTo(map);
    const markers = new Map();

    function placeLat(place) {{
      return place.lat || place.osm_candidate?.lat;
    }}
    function placeLon(place) {{
      return place.lon || place.osm_candidate?.lon;
    }}
    function markerStyle(place, selected = false) {{
      if (selected) {{
        return {{
          radius: 12,
          color: "#4a1430",
          weight: 4,
          fillColor: "#d12f7a",
          fillOpacity: 0.96
        }};
      }}
      return {{
        radius: place.importance === "primary" ? 8 : 6,
        color: place.review_status === "needs_historical_map" ? "#a97822" : "#315f8f",
        weight: 2,
        fillColor: place.review_status === "seeded" ? "#2f6f5e" : "#fff3c9",
        fillOpacity: 0.85
      }};
    }}
    function updateSelectedMarker() {{
      for (const place of places) {{
        const marker = markers.get(place.id);
        if (!marker) continue;
        marker.setStyle(markerStyle(place, place.id === state.selectedId));
        if (place.id === state.selectedId) marker.bringToFront();
      }}
    }}
    function chipClass(status) {{
      if (status === "needs_historical_map") return "chip map";
      if (status === "needs_confirmation" || status === "needs_resolution") return "chip need";
      return "chip";
    }}
    function visitChipClass(status) {{
      if (status === "private_residence") return "chip private";
      if (status === "needs_visit_research") return "chip need";
      return "chip visit";
    }}
    const VISITABLE_STATUSES = new Set([
      "active_campus_site",
      "active_church_or_successor_site",
      "active_library_successor_site",
      "active_municipal_building",
      "house_museum",
      "museum_site",
      "operating_hotel_successor",
      "public_historic_cemetery",
      "public_historic_park",
      "public_state_reservation",
      "public_state_reservation_marker",
      "seasonal_house_museum",
      "seasonal_nps_site",
      "site_on_museum_grounds",
      "state_historic_site"
    ]);
    function visitBucket(place) {{
      const status = place.visitability?.visit_status;
      if (status === "private_residence") return "private_residence";
      if (VISITABLE_STATUSES.has(status)) return "visitable";
      return status || "needs_visit_research";
    }}
    const STATUS_LABELS = {{
      needs_historical_map: "needs old-map proof",
      needs_resolution: "needs place disambiguation",
      needs_confirmation: "needs source confirmation",
      seeded: "seeded",
      accepted: "accepted",
      candidate: "candidate",
      forward_search: "forward search",
      reverse_nearest: "nearest reverse lookup",
      needs_visit_research: "visit status unknown",
      private_residence: "private residence",
      active_municipal_building: "active municipal building",
      active_church_or_successor_site: "active church/successor site",
      active_campus_site: "active campus site",
      active_library_successor_site: "active library/successor",
      house_museum: "house museum",
      seasonal_house_museum: "seasonal house museum",
      seasonal_nps_site: "seasonal NPS site",
      public_historic_park: "public historic park",
      public_historic_cemetery: "public historic cemetery",
      public_state_reservation: "public state reservation",
      public_state_reservation_marker: "state reservation marker",
      museum_site: "museum site",
      site_on_museum_grounds: "museum grounds site",
      state_historic_site: "state historic site",
      operating_hotel_successor: "operating hotel/successor"
    }};
    const STATUS_HELP = {{
      needs_historical_map: "A modern OSM object or street name is not enough. Use period maps, archival sources, or stable historical references before accepting a precise point.",
      needs_resolution: "The text names a broad area or ambiguous place. Narrow it to a specific site, route, or locality before mapping.",
      needs_confirmation: "There is a plausible modern or historical candidate, but it still needs a confirming source.",
      seeded: "The place is seeded as a reviewable authority record, but individual events may still need finer-grained sites."
    }};
    function statusLabel(status) {{
      if (!status) return "unknown";
      return STATUS_LABELS[status] || status.replaceAll("_", " ");
    }}
    function fillSelect(id, values) {{
      const select = document.getElementById(id);
      for (const value of values) {{
        const option = document.createElement("option");
        option.value = value;
        option.textContent = statusLabel(value);
        select.appendChild(option);
      }}
    }}
    fillSelect("status", [...new Set(places.map((place) => place.review_status))]);
    fillSelect("importance", [...new Set(places.map((place) => place.importance))]);
    document.getElementById("stats").innerHTML = `
      <div class="stat"><strong>${{catalog.counts.places}}</strong><span>places</span></div>
      <div class="stat"><strong>${{catalog.counts.book_citations}}</strong><span>book citations</span></div>
      <div class="stat"><strong>${{catalog.counts.chronology_events}}</strong><span>event links</span></div>
      <div class="stat"><strong>${{catalog.counts.places_with_web_confirmations}}</strong><span>web confirmed</span></div>
      <div class="stat"><strong>${{catalog.counts.places_with_current_addresses}}</strong><span>addresses</span></div>
      <div class="stat"><strong>${{catalog.counts.visitable_places}}</strong><span>visitable</span></div>
      <div class="stat"><strong>${{catalog.counts.places_with_visit_research}}</strong><span>visit checked</span></div>
      <div class="stat"><strong>${{catalog.counts.places_needing_historical_map}}</strong><span>need proof</span></div>
    `;

    for (const place of places) {{
      const lat = Number(placeLat(place));
      const lon = Number(placeLon(place));
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const marker = L.circleMarker([lat, lon], markerStyle(place, place.id === state.selectedId)).addTo(map);
      marker.bindTooltip(place.canonical_name);
      marker.on("click", () => selectPlace(place.id, true));
      markers.set(place.id, marker);
    }}

    function filteredPlaces() {{
      const query = document.getElementById("search").value.trim().toLowerCase();
      const status = document.getElementById("status").value;
      const importance = document.getElementById("importance").value;
      const visit = document.getElementById("visit").value;
      return places.filter((place) => {{
        if (status && place.review_status !== status) return false;
        if (importance && place.importance !== importance) return false;
        if (visit && visitBucket(place) !== visit) return false;
        if (!query) return true;
        return [
          place.canonical_name,
          place.kind,
          place.review_status,
          place.importance,
          place.historical_map_need,
          place.visitability?.visit_status,
          place.visitability?.survival_status,
          place.visitability?.visibility,
          place.visitability?.visitor_note,
          ...(place.web_refs || []).flatMap((ref) => [ref.label, ref.note, ref.type]),
          ...(place.aliases || [])
        ].join(" ").toLowerCase().includes(query);
      }});
    }}

    function renderList() {{
      const list = document.getElementById("place-list");
      list.innerHTML = "";
      for (const place of filteredPlaces()) {{
        const button = document.createElement("button");
        button.className = "place-row" + (place.id === state.selectedId ? " active" : "");
        button.innerHTML = `
          <strong>${{place.canonical_name}}</strong>
          <span class="subtle">${{place.kind}} - ${{place.book_citation_count}} book citations - ${{place.chronology_event_count}} event links - ${{place.web_confirmation_count}} web confirmations</span>
          <span class="chips">
            <span class="${{chipClass(place.review_status)}}">${{statusLabel(place.review_status)}}</span>
            <span class="${{visitChipClass(place.visitability?.visit_status)}}">${{statusLabel(place.visitability?.visit_status)}}</span>
            ${{place.web_confirmation_count ? `<span class="chip source">${{place.web_confirmation_count}} web confirmations</span>` : `<span class="chip need">no web confirmation</span>`}}
            <span class="chip">${{place.importance}}</span>
            ${{place.osm_candidate ? `<span class="chip osm">${{statusLabel(place.osm_candidate.review_status)}} OSM</span>` : ""}}
          </span>
        `;
        button.addEventListener("click", () => selectPlace(place.id, true));
        list.appendChild(button);
      }}
    }}

    function renderDetails() {{
      const place = places.find((candidate) => candidate.id === state.selectedId) || places[0];
      if (!place) {{
        document.getElementById("details").textContent = "No places loaded.";
        return;
      }}
      const osmLink = place.osm_candidate?.url || place.osm_search_url;
      const osmLabel = place.osm_candidate?.url ? "Open OSM candidate" : "Search OpenStreetMap";
      const oldMapLink = `old_map_georeference.html?place=${{encodeURIComponent(place.id)}}`;
      const addressHtml = place.current_address
        ? `<p><strong>Current address:</strong> ${{place.current_address}}</p>`
        : '<p class="subtle">No current address has been resolved yet.</p>';
      const controlPoints = catalog.georeference?.control_points || [];
      const placeControlPoints = controlPoints.filter((point) => point.place_id === place.id);
      const visit = place.visitability || {{}};
      const visitSources = visit.sources || [];
      const visitSourceHtml = visitSources.length
        ? visitSources.map((ref) => `<p><a href="${{ref.url}}" target="_blank" rel="noreferrer">${{ref.label || ref.url}}</a>${{ref.type ? ` - ${{statusLabel(ref.type)}}` : ""}}</p>`).join("")
        : '<p class="subtle">No current visit/survival source has been attached yet.</p>';
      const mentionHtml = place.book_mentions.length
        ? place.book_mentions.map((mention) => `
            <div class="mention">
              <a href="${{mention.close_reading_url}}" target="_blank" rel="noreferrer">
                Chapter ${{mention.chapter_index}}: ${{mention.chapter_title}}, cell ${{mention.cell_index}}
              </a>
              <div>${{mention.snippet}}</div>
              <div class="subtle">Matched: ${{mention.matched_aliases.join(", ")}}</div>
            </div>
          `).join("")
        : '<p class="subtle">No direct seed-alias mention found yet.</p>';
      const eventHtml = place.chronology_events.length
        ? place.chronology_events.map((event) => {{
            const firstBookHref = event.book_anchors.find((anchor) => anchor.start?.href)?.start?.href;
            const firstSourceHref = event.source_keys.find((source) => source.url)?.url;
            const publicEvidenceHref = firstBookHref || firstSourceHref || event.public_chronology_url;
            const publicEvidenceLabel = firstBookHref
              ? "Open public Close Reading evidence"
              : firstSourceHref
                ? "Open public web evidence"
                : "Open public chronology page";
            const anchorLinks = event.book_anchors.flatMap((anchor) => {{
              const links = [];
              if (anchor.start?.href) links.push(`<a href="${{anchor.start.href}}" target="_blank" rel="noreferrer">Close Reading book.md:${{anchor.line_range}} start</a>`);
              if (anchor.end?.href && anchor.end.href !== anchor.start?.href) links.push(`<a href="${{anchor.end.href}}" target="_blank" rel="noreferrer">Close Reading end</a>`);
              return links;
            }}).join(" ");
            const sourceLinks = event.source_keys
              .filter((source) => source.url)
              .map((source) => `<a href="${{source.url}}" target="_blank" rel="noreferrer">${{source.key}}</a>`)
              .join(" ");
            return `
              <div class="mention">
                <a href="${{publicEvidenceHref}}" target="_blank" rel="noreferrer">${{publicEvidenceLabel}}</a>
                <div><strong>${{event.headline}}</strong></div>
                <div>${{event.summary || ""}}</div>
                <div class="subtle">Matched: ${{event.matched_aliases.join(", ")}}</div>
                <div class="subtle">Chronology source: <code>${{event.local_path}}:${{event.line}}</code>${{event.public_event_id ? `; public event id: <code>${{event.public_event_id}}</code>` : ""}}</div>
                ${{anchorLinks ? `<div class="subtle">Book anchors: ${{anchorLinks}}</div>` : ""}}
                ${{sourceLinks ? `<div class="subtle">Web/source keys: ${{sourceLinks}}</div>` : ""}}
              </div>
            `;
          }}).join("")
        : '<p class="subtle">No matched character-chronology event yet.</p>';
      const chronologyHtml = place.chronology_mentions.length
        ? place.chronology_mentions.map((mention) => `
            <div class="mention">
              <div><code>${{mention.local_path}}:${{mention.line}}</code></div>
              <div>${{mention.snippet}}</div>
              <div class="subtle">Matched: ${{mention.matched_aliases.join(", ")}}</div>
            </div>
          `).join("")
        : '<p class="subtle">No chronology seed-alias mention found yet.</p>';
      const candidateHtml = place.osm_candidate
        ? `<p>${{place.osm_candidate.display_name || ""}}</p>
           <p class="subtle"><code>${{place.osm_candidate.osm_type || ""}}/${{place.osm_candidate.osm_id || ""}}</code>
           ${{place.osm_candidate.category || ""}}/${{place.osm_candidate.type || ""}} -
           ${{statusLabel(place.osm_candidate.match_method || "unknown")}}</p>
           ${{place.osm_candidate.review_note ? `<p>${{place.osm_candidate.review_note}}</p>` : ""}}`
        : '<p class="subtle">No cached Nominatim candidate yet.</p>';
      const webHtml = `
        ${{place.web_refs?.length
          ? place.web_refs.map((ref) => `<p><a href="${{ref.url}}" target="_blank" rel="noreferrer">${{ref.label || ref.url}}</a>${{ref.note ? ` - ${{ref.note}}` : ""}}</p>`).join("")
          : '<p class="subtle">No curated site-level web confirmation yet.</p>'}}
        <p class="subtle"><a href="${{place.web_search_url}}" target="_blank" rel="noreferrer">Search the web for more confirmation</a></p>
      `;
      const georefHtml = placeControlPoints.length
        ? placeControlPoints.map((point) => `
            <div class="mention">
              <div><strong>${{point.label}}</strong></div>
              <div>Old map pixel: <code>${{point.old_map_pixel.x}}, ${{point.old_map_pixel.y}}</code>; modern WGS84: <code>${{point.modern_wgs84.lat}}, ${{point.modern_wgs84.lon}}</code></div>
              <div class="subtle">${{point.note}}</div>
            </div>
          `).join("")
        : `<p class="subtle">No control point attached to this place yet. The map currently has ${{controlPoints.length}} draft control points for georeferencing the 1852 Walling map.</p>`;
      document.getElementById("details").innerHTML = `
        <h2>${{place.canonical_name}}</h2>
        <div class="subtle">${{place.kind}} - ${{place.coordinate_quality}}</div>
        <div class="chips">
          <span class="${{chipClass(place.review_status)}}">${{statusLabel(place.review_status)}}</span>
          <span class="${{visitChipClass(visit.visit_status)}}">${{statusLabel(visit.visit_status)}}</span>
          ${{place.web_confirmation_count ? `<span class="chip source">${{place.web_confirmation_count}} web confirmations</span>` : `<span class="chip need">no web confirmation</span>`}}
          <span class="chip">${{place.importance}}</span>
          ${{place.osm_candidate ? `<span class="chip osm">${{statusLabel(place.osm_candidate.review_status)}} OSM</span>` : ""}}
          <span class="chip">${{place.book_citation_count}} book citations</span>
          <span class="chip">${{place.chronology_event_count}} event links</span>
        </div>
        <div class="actions">
          <a class="action" href="${{osmLink}}" target="_blank" rel="noreferrer">${{osmLabel}}</a>
          <a class="action secondary" href="${{place.public_atlas_url}}" target="_blank" rel="noreferrer">Public atlas link</a>
          <a class="action secondary" href="${{place.google_maps_url}}" target="_blank" rel="noreferrer">Open in Google Maps</a>
          <a class="action secondary" href="${{oldMapLink}}" target="_blank" rel="noreferrer">Old map page</a>
          <a class="action secondary" href="https://www.loc.gov/item/2012593522/" target="_blank" rel="noreferrer">1852 Concord map</a>
          <a class="action pending" href="https://www.loc.gov/maps/?fa=location:massachusetts&fo=json&q=concord" target="_blank" rel="noreferrer">LOC map search</a>
        </div>
        <section>
          <h3>Current Address</h3>
          ${{addressHtml}}
          <p><a href="${{place.google_maps_url}}" target="_blank" rel="noreferrer">Open this location in Google Maps</a></p>
        </section>
        <section>
          <h3>Aliases</h3>
          <p>${{place.aliases.map((alias) => `<code>${{alias}}</code>`).join(" ")}}</p>
        </section>
        <section>
          <h3>Visit Today</h3>
          <div class="chips">
            <span class="${{visitChipClass(visit.visit_status)}}">${{statusLabel(visit.visit_status)}}</span>
            <span class="chip">${{statusLabel(visit.survival_status)}}</span>
            <span class="chip">${{statusLabel(visit.visibility)}}</span>
          </div>
          <p>${{visit.visitor_note || ""}}</p>
          ${{visitSourceHtml}}
        </section>
        <section>
          <h3>Review State</h3>
          <p>${{STATUS_HELP[place.review_status] || ""}}</p>
          <p>${{place.historical_map_need}}</p>
        </section>
        <section>
          <h3>Web Confirmations</h3>
          ${{webHtml}}
        </section>
        <section>
          <h3>OSM Candidate</h3>
          ${{candidateHtml}}
          <p class="subtle">Query: <code>${{place.osm_query}}</code></p>
        </section>
        <section>
          <h3>Old-Map Control Points</h3>
          ${{georefHtml}}
        </section>
        <section>
          <h3>Book Citations</h3>
          ${{mentionHtml}}
        </section>
        <section>
          <h3>Chronology Events</h3>
          ${{eventHtml}}
        </section>
        <section>
          <h3>Main Chronology Line Mentions</h3>
          ${{chronologyHtml}}
        </section>
      `;
    }}

    function selectPlace(id, moveMap) {{
      state.selectedId = id;
      const place = places.find((candidate) => candidate.id === id);
      renderList();
      renderDetails();
      updateSelectedMarker();
      if (moveMap && place) {{
        const marker = markers.get(id);
        if (marker) {{
          map.setView(marker.getLatLng(), place.kind.includes("settlement") ? 11 : 15);
          marker.openTooltip();
        }}
      }}
    }}

    document.getElementById("search").addEventListener("input", renderList);
    for (const id of ["status", "importance", "visit"]) {{
      const control = document.getElementById(id);
      control.addEventListener("input", renderList);
      control.addEventListener("change", renderList);
    }}
    renderList();
    renderDetails();
    updateSelectedMarker();
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def write_old_map_html(catalog: dict) -> None:
    georeference = catalog.get("georeference", {})
    place_names = {place["id"]: place["canonical_name"] for place in catalog.get("places", [])}
    payload = html_escape_json({"georeference": georeference, "place_names": place_names})
    OLD_MAP_HTML_PATH.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>1852 Concord Old-Map Anchors</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    :root {{
      color-scheme: light;
      --ink: #202124;
      --muted: #5b626a;
      --line: #d9ded7;
      --paper: #fbfaf6;
      --panel: #ffffff;
      --green: #2f6f5e;
      --blue: #315f8f;
      --gold: #a97822;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      background: #f6f2ea;
    }}
    h1 {{ margin: 0 0 4px; font-size: 23px; letter-spacing: 0; }}
    a {{ color: var(--blue); font-weight: 650; text-decoration: none; }}
    .subtle {{ color: var(--muted); }}
    main {{
      display: grid;
      grid-template-columns: minmax(320px, 390px) 1fr;
      min-height: calc(100vh - 82px);
    }}
    aside {{
      padding: 16px;
      border-right: 1px solid var(--line);
      background: var(--panel);
      overflow: auto;
    }}
    #old-map {{ min-height: calc(100vh - 82px); background: #e8e0d0; }}
    .point {{
      border-left: 3px solid #bda14d;
      padding: 9px 10px;
      margin: 10px 0;
      background: #fffaf0;
      border-radius: 0 6px 6px 0;
    }}
    .point.active {{
      border-left-color: #d12f7a;
      background: #fff2f7;
      box-shadow: inset 0 0 0 1px #efbad0;
    }}
    .oldmap-legend {{
      position: absolute;
      z-index: 500;
      right: 12px;
      bottom: 16px;
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
      color: var(--muted);
      font-size: 12px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
    }}
    .legend-dot {{
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 999px;
      margin-right: 5px;
      background: #d12f7a;
      border: 2px solid #4a1430;
      vertical-align: -1px;
    }}
    code {{
      background: #f0eee8;
      border: 1px solid #e2ddd1;
      border-radius: 4px;
      padding: 1px 4px;
    }}
    @media (max-width: 860px) {{
      header {{ display: block; }}
      main {{ grid-template-columns: 1fr; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      #old-map {{ min-height: 72vh; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>1852 Concord Old-Map Anchors</h1>
      <div class="subtle">Draft control points on H. F. Walling's 1852 Concord map. This is not a finished geo-rectification.</div>
    </div>
    <div><a id="atlas-back" href="fuller_location_atlas.html">Back to atlas</a></div>
  </header>
  <main>
    <aside>
      <h2>Draft Anchors</h2>
      <p class="subtle">These are the old-map pixels currently identified by visual inspection. The next pass should geo-rectify the map and check residual error before house-level claims.</p>
      <p id="selected-place-note" class="subtle"></p>
      <div id="point-list"></div>
    </aside>
    <div id="old-map"></div>
  </main>
  <script id="old-map-data" type="application/json">{payload}</script>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const payload = JSON.parse(document.getElementById("old-map-data").textContent);
    const georef = payload.georeference || {{}};
    const oldMap = georef.old_map || {{}};
    const points = georef.control_points || [];
    const names = payload.place_names || {{}};
    const currentPlaceId = new URLSearchParams(window.location.search).get("place");
    const currentPlaceName = names[currentPlaceId];
    const selectedNote = document.getElementById("selected-place-note");
    if (currentPlaceId) {{
      document.getElementById("atlas-back").href = `fuller_location_atlas.html?place=${{encodeURIComponent(currentPlaceId)}}`;
      selectedNote.textContent = currentPlaceName
        ? `Current atlas place: ${{currentPlaceName}}`
        : `Current atlas place id: ${{currentPlaceId}}`;
    }}
    const size = oldMap.local_reference_image_size || {{ width: 2354, height: 1863 }};
    const bounds = [[0, 0], [size.height, size.width]];
    const map = L.map("old-map", {{
      crs: L.CRS.Simple,
      minZoom: -4,
      maxZoom: 3,
      zoomSnap: 0.25
    }});
    L.imageOverlay(oldMap.local_reference_image || "maps/loc_concord_1852_walling_pct25.jpg", bounds).addTo(map);
    map.fitBounds(bounds);
    const list = document.getElementById("point-list");
    let selectedPoint = null;
    for (const point of points) {{
      const y = Number(point.old_map_pixel?.y);
      const x = Number(point.old_map_pixel?.x);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      const selected = point.place_id === currentPlaceId;
      const title = names[point.place_id] || point.label || point.place_id;
      const atlasUrl = `fuller_location_atlas.html?place=${{encodeURIComponent(point.place_id)}}`;
      L.circleMarker([y, x], {{
        radius: selected ? 12 : 7,
        color: selected ? "#4a1430" : "#6d4d00",
        weight: selected ? 4 : 2,
        fillColor: selected ? "#d12f7a" : "#ffcf58",
        fillOpacity: selected ? 0.96 : 0.9
      }}).addTo(map).bindPopup(`
        <strong>${{title}}</strong><br>
        Old-map pixel: <code>${{x}}, ${{y}}</code><br>
        Modern WGS84: <code>${{point.modern_wgs84?.lat}}, ${{point.modern_wgs84?.lon}}</code><br>
        <a href="${{atlasUrl}}">Open atlas place</a>
      `);
      if (selected) selectedPoint = [y, x];
      const item = document.createElement("div");
      item.className = "point" + (selected ? " active" : "");
      item.innerHTML = `
        <strong>${{title}}</strong>
        <div>Old-map pixel: <code>${{x}}, ${{y}}</code></div>
        <div>Modern WGS84: <code>${{point.modern_wgs84?.lat}}, ${{point.modern_wgs84?.lon}}</code></div>
        <div class="subtle">${{point.note || ""}}</div>
        <div><a href="${{atlasUrl}}">Open atlas place</a></div>
      `;
      item.addEventListener("click", () => map.setView([y, x], 0));
      list.appendChild(item);
    }}
    if (currentPlaceId && !selectedPoint) {{
      const item = document.createElement("div");
      item.className = "point active";
      item.innerHTML = `
        <strong>${{currentPlaceName || currentPlaceId}}</strong>
        <div class="subtle">No old-map control point has been attached to this atlas place yet.</div>
      `;
      list.prepend(item);
    }}
    if (selectedPoint) {{
      const legend = L.control({{ position: "bottomright" }});
      legend.onAdd = () => {{
        const div = L.DomUtil.create("div", "oldmap-legend");
        div.innerHTML = '<span class="legend-dot"></span>current place';
        return div;
      }};
      legend.addTo(map);
      map.setView(selectedPoint, 0);
    }}
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )
    INDEX_PATH.write_text(
        """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta http-equiv="refresh" content="0; url=fuller_location_atlas.html">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="canonical" href="fuller_location_atlas.html">
    <title>Thoreau Location Atlas</title>
  </head>
  <body>
    <p><a href="fuller_location_atlas.html">Open the Fuller location atlas.</a></p>
    <p><a href="old_map_georeference.html">Open the 1852 Concord old-map anchors.</a></p>
  </body>
</html>
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-examples", type=int, default=8)
    args = parser.parse_args()
    seed = load_json(SEED_PATH, None)
    if seed is None:
        raise SystemExit(f"Missing seed file: {SEED_PATH}")
    places = enrich(seed, max_examples=args.max_examples)
    write_catalog(seed, places)
    write_mentions_csv(places)
    write_events_csv(places)
    catalog = load_json(CATALOG_PATH, {})
    write_html(catalog)
    write_old_map_html(catalog)
    print(f"Wrote {CATALOG_PATH.relative_to(WORKSPACE)}")
    print(f"Wrote {MENTIONS_CSV_PATH.relative_to(WORKSPACE)}")
    print(f"Wrote {EVENTS_CSV_PATH.relative_to(WORKSPACE)}")
    print(f"Wrote {HTML_PATH.relative_to(WORKSPACE)}")
    print(f"Wrote {OLD_MAP_HTML_PATH.relative_to(WORKSPACE)}")


if __name__ == "__main__":
    main()
