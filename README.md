# Thoreau Location Atlas

This workspace catalogs places mentioned in books by Henry David Thoreau and
in Thoreau biographies. The pilot source is Randall Fuller's *The Book That
Changed America: How Darwin's Theory of Evolution Ignited a Nation*.

The folder sits beside `thoreau_biographies_chronology/` because this is a
research/export workspace, not app code. The chronology Markdown remains useful
context, but this workspace treats places as the primary unit.

Public site:

- <https://rahuldave.com/thoreau_locations_osm/>

## Current Pilot

- Source book: Close Reading book `125`.
- Local database: `../close_reading/data/close_reading.sqlite`.
- Chronology context:
  `../thoreau_biographies_chronology/the_book_that_changed_america_chronology.md`.
- Review interface: `index.html`, which redirects to
  `fuller_location_atlas.html`.
- Old-map anchor page: `old_map_georeference.html`.
- Generated catalog: `data/fuller_place_catalog.json`.
- Mention table: `data/fuller_place_mentions.csv`.
- Character-chronology event/place table:
  `data/fuller_place_chronology_events.csv`.
- OSM/Nominatim cache: `data/osm_geocode_cache.json`.
- Historical and web source register: `data/confirmation_sources.json`.
- Old-map control points: `data/georeference_control_points.json`.
- Current survival/visitability register: `data/visitability_sources.json`.
- Seed gazetteer/review queue: `data/fuller_place_seed.json`.

The current Fuller pilot seed file contains 69 places, but the generated atlas
shows only the 39 site-level records. It suppresses broad context rows such as
countries, states, cities, counties, regions, rivers, roads, and canals because
they are not useful targets for OSM/address review. Suppressed context rows are
kept in `data/fuller_place_catalog.json` only as an audit list.

The generated atlas records direct book mentions from SQLite, event links from
the Thoreau/Sanborn/Brace/Bronson Alcott/Emerson chronology Markdown files,
chronology-line mentions from the main Fuller chronology, OpenStreetMap search
links, cached Nominatim candidates, written current addresses where available,
Google Maps links, current visit/survival status, curated web confirmations,
and an explicit review state for historical or ambiguous places. The selected
place is highlighted in magenta on the modern map; old-map links preserve the
selected place so any matching 1852-map control point is highlighted there too.

## Evidence Standard

Each place should move through six layers:

1. Text evidence: direct mentions from the close-reading SQLite cells, with
   chapter/cell links back to the reader.
2. Chronology evidence: matching lines from the existing chronology Markdown
   when the place is part of an already-investigated event.
3. OSM evidence: Nominatim or OSM candidate objects. These are candidates, not
   proof, unless the record is marked accepted.
4. Current-place evidence: written current address when available, plus Google
   Maps and OSM links for physical navigation and coordinate review.
5. Visit evidence: whether the historical building/site appears to survive,
   whether it is public, seasonal, private, or unresolved, and what a visitor can
   see today.
6. Historical/web confirmation: external web sources and, where needed,
   historical maps or archival references.

Do not assign precise coordinates to historical residences, schools, wharves,
or meeting rooms from a modern street match alone. Examples that still need
historical confirmation include Franklin Sanborn's house on Sudbury Road,
Thoreau's family house on Main Street, the Concord Lyceum venue, Boston Wharf,
and Emerson's Walden woodlot.

The UI uses chips rather than formal tags. The visitability chip is the one to
use for "visitable" status: filter by `Visitable / public-ish`, `Private
residences`, or `Needs visit research`.

The UI label `needs old-map proof` means a modern address or OSM hit is not
enough. It needs a period-map, deed, archival, or equivalent historical source
before a precise point should be accepted. This matters especially for private
residences and buildings whose names, street addresses, or functions changed.

## Old Maps

The first confirmed old-map anchor is the Library of Congress record for H. F.
Walling's 1852 *Map of the town of Concord, Middlesex County Mass*:

- Record: <https://www.loc.gov/item/2012593522/>
- Image resource: <https://www.loc.gov/resource/g3764c.ct001110/>

The LOC metadata says the map includes an ancillary Concord village map and
White Pond/Walden Pond surveys by H. D. Thoreau. This should be the first
Concord map checked when resolving 1860 Concord scenes. Later LOC Sanborn fire
insurance maps for Concord, especially 1909 and 1918, are useful continuity
checks but are not contemporary with Fuller's 1860 dinner scene.

`data/georeference_control_points.json` starts a draft geo-rectification
workflow for the 1852 Walling map. It records old-map pixel points and modern
WGS84 points for Concord village/Town House, Walden Pond, White Pond, and Old
North Bridge. `old_map_georeference.html` displays those four draft anchors on
the scanned old map. These are manual draft control points; refine them against
higher resolution map crops and check residual error before using the transform
for house-level claims such as Sanborn's house or Thoreau's Main Street home.

## Visit Layer

The atlas is meant to support physical visits without blurring historical
claims. `data/visitability_sources.json` separates:

- `visit_status`: public site, seasonal museum, active civic building, private
  residence, or still unknown.
- `survival_status`: whether the original structure survives, the site is
  marked, a successor/reproduction stands, or the fact still needs research.
- `visibility`: what a visitor can reasonably see today.

For example, the Sanborn/Channing-Fuller-Sanborn candidate at 325 Main Street
is marked as a surviving private-residence candidate, viewable only from the
public way, while the Concord Town House/Lyceum candidate is marked as an
active municipal building whose 1851 Town House identity is supported by
current local-history and civic sources but whose exact Lyceum relationship
still needs source-specific confirmation.

## Regeneration

Refresh OSM candidates cautiously:

```bash
python3 thoreau_locations_osm/scripts/osm_geocode.py
```

The geocoder script uses Nominatim with a custom user agent and a delay between
requests. Use `--ids <place_id>...` for a focused refresh.

Rebuild the catalog and HTML:

```bash
python3 thoreau_locations_osm/scripts/build_fuller_atlas.py
```

Open the interface locally:

```bash
open thoreau_locations_osm/index.html
```

## Expansion Plan

After the Fuller interface feels right, expand in this order:

1. Add source manifests for Thoreau's own works from `thoreau_complete_works_books/`.
2. Add the major Thoreau biographies from `thoreau_biographies/`.
3. Promote recurring places into a shared place authority file.
4. Add source-specific mention tables so the same place can show its route
   across Thoreau's writings, biographies, and historical maps.
5. Add a reviewed export for accepted OSM/Wikidata candidates and unresolved
   historical-map work.
