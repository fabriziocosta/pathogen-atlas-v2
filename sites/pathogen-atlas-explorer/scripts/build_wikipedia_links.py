#!/usr/bin/env python3
"""Resolve exact Wikipedia article titles for the plotted species list.

The generated CSV is an explicit, resumable lookup table: it contains one row
for every species in the Phase 5 coordinate release, including species with no
matching English Wikipedia article. API redirects and title normalization are
followed; fuzzy search results are intentionally not treated as matches.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
COORDINATES = ROOT / "data/05_plots/release/species_feature_space_coordinates.parquet"
OUTPUT = ROOT / "sites/pathogen-atlas-explorer/data/species_wikipedia_links.csv"
API_URL = "https://en.wikipedia.org/w/api.php"
BATCH_SIZE = 25
USER_AGENT = "PathogenAtlasExplorer/1.0 (species article link resolver)"


def request_batch(names: list[str]) -> dict[str, dict[str, str | None]]:
    """Resolve requested page titles, following MediaWiki normalization/redirects."""
    query = urlencode(
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
            "maxlag": "5",
            "titles": "|".join(names),
        }
    )
    request = Request(f"{API_URL}?{query}", headers={"User-Agent": USER_AGENT})
    for attempt in range(7):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if payload.get("error"):
                raise RuntimeError(f"Wikipedia API returned an error: {payload['error']}")
            break
        except (HTTPError, URLError, TimeoutError) as error:
            if attempt == 6:
                raise RuntimeError(f"Wikipedia API request failed: {error}") from error
            retry_after = error.headers.get("Retry-After") if isinstance(error, HTTPError) else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(5 * (2**attempt), 60)
            time.sleep(delay)

    query_data = payload.get("query", {})
    title_map: dict[str, str] = {}
    for key in ("normalized", "redirects"):
        for mapping in query_data.get(key, []):
            title_map[mapping["from"].casefold()] = mapping["to"]
    pages = query_data.get("pages", [])
    found_by_title = {
        page["title"].casefold(): page["title"]
        for page in pages
        if "missing" not in page
    }

    resolved: dict[str, dict[str, str | None]] = {}
    for requested in names:
        title = requested
        visited: set[str] = set()
        while title.casefold() in title_map and title.casefold() not in visited:
            visited.add(title.casefold())
            title = title_map[title.casefold()]
        canonical_title = found_by_title.get(title.casefold())
        if canonical_title:
            url = "https://en.wikipedia.org/wiki/" + quote(canonical_title.replace(" ", "_"), safe="()_,-.")
            resolved[requested] = {
                "wikipedia_title": canonical_title,
                "wikipedia_url": url,
                "match_status": "found",
                "match_type": "exact_or_redirect",
            }
        else:
            resolved[requested] = {
                "wikipedia_title": "",
                "wikipedia_url": "",
                "match_status": "not_found",
                "match_type": "exact_title_query",
            }
    return resolved


def write_table(rows: dict[str, dict[str, str | None]]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["canonical_species", "wikipedia_title", "wikipedia_url", "match_status", "match_type"],
        )
        writer.writeheader()
        for name in sorted(rows, key=str.casefold):
            writer.writerow({"canonical_species": name, **rows[name]})


def main() -> None:
    if not COORDINATES.exists():
        raise FileNotFoundError(f"Build Phase 5 coordinates first: {COORDINATES}")
    names = sorted(
        pd.read_parquet(COORDINATES, columns=["canonical_species"])["canonical_species"]
        .dropna().astype(str).str.strip().unique(),
        key=str.casefold,
    )
    if not names:
        raise ValueError("Coordinate table contains no species names.")

    rows: dict[str, dict[str, str | None]] = {}
    if OUTPUT.exists():
        previous = pd.read_csv(OUTPUT, keep_default_na=False)
        if {"canonical_species", "wikipedia_title", "wikipedia_url", "match_status", "match_type"}.issubset(previous.columns):
            allowed = set(names)
            for row in previous.to_dict(orient="records"):
                name = str(row["canonical_species"])
                if name in allowed and row["match_status"] in {"found", "not_found"}:
                    rows[name] = {column: str(row[column]) for column in ("wikipedia_title", "wikipedia_url", "match_status", "match_type")}

    pending = [name for name in names if name not in rows]
    print(f"Resolving {len(pending):,} species; reusing {len(rows):,} existing rows.", flush=True)
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        rows.update(request_batch(batch))
        write_table(rows)
        print(f"Resolved {min(start + len(batch), len(pending)):,}/{len(pending):,} pending species.", flush=True)
        time.sleep(1.1)

    # Ensure the output covers the coordinate release exactly, in sorted order.
    write_table({name: rows[name] for name in names})
    found = sum(row["match_status"] == "found" for row in rows.values())
    print(f"Wrote {OUTPUT.relative_to(ROOT)}: {len(rows):,} species, {found:,} articles found.")


if __name__ == "__main__":
    main()
