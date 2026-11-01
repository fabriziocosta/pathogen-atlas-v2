#!/usr/bin/env python3
"""Resolve exact GBIF Backbone species pages for the plotted species list.

The CSV is an explicit, resumable lookup table: one row is retained for every
coordinate species, including taxa with no exact GBIF species match.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
COORDINATES = ROOT / "data/05_plots/release/species_feature_space_coordinates.parquet"
SPECIES_SUMMARY = ROOT / "data/04_tables/release/species_summary.parquet"
OUTPUT = ROOT / "sites/pathogen-atlas-explorer/data/species_gbif_links.csv"
API_URL = "https://api.gbif.org/v2/species/match"
BATCH_SIZE = 100
USER_AGENT = "PathogenAtlasExplorer/1.0 (GBIF species page link resolver)"
FIELDS = [
    "canonical_species",
    "gbif_taxon_key",
    "gbif_scientific_name",
    "gbif_url",
    "match_status",
    "match_type",
    "confidence",
]


def request_batch(names: list[str], domains: dict[str, str]) -> dict[str, dict[str, str | int | None]]:
    """Match a batch against GBIF's taxonomic backbone, accepting exact species only."""
    body = json.dumps(
        [{"scientificName": name, "kingdom": domains.get(name)} for name in names]
    ).encode("utf-8")
    request = Request(
        API_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    for attempt in range(7):
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.load(response)
            break
        except (HTTPError, URLError, TimeoutError) as error:
            if attempt == 6:
                raise RuntimeError(f"GBIF species matching request failed: {error}") from error
            retry_after = error.headers.get("Retry-After") if isinstance(error, HTTPError) else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 * (2**attempt), 60)
            time.sleep(delay)

    if not isinstance(payload, list) or len(payload) != len(names):
        result_count = len(payload) if isinstance(payload, list) else "an invalid number of"
        raise RuntimeError(f"GBIF returned {result_count} results for {len(names)} names.")

    matches: dict[str, dict[str, str | int | None]] = {}
    for requested, result in zip(names, payload):
        usage = result.get("usage") or {}
        diagnostics = result.get("diagnostics") or {}
        match_type = str(diagnostics.get("matchType") or "NONE")
        canonical_name = str(usage.get("canonicalName") or "")
        taxon_key = str(usage.get("key") or "")
        exact_species = (
            bool(taxon_key)
            and str(usage.get("rank") or "").upper() == "SPECIES"
            and match_type == "EXACT"
            and canonical_name.casefold() == requested.casefold()
        )
        matches[requested] = {
            "gbif_taxon_key": taxon_key if exact_species else "",
            "gbif_scientific_name": str(usage.get("name") or "") if exact_species else "",
            "gbif_url": f"https://www.gbif.org/species/{quote(taxon_key, safe='')}" if exact_species else "",
            "match_status": "found" if exact_species else "not_found",
            "match_type": match_type,
            "confidence": diagnostics.get("confidence"),
        }
    return matches


def write_table(rows: dict[str, dict[str, str | int | None]]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for name in sorted(rows, key=str.casefold):
            writer.writerow({"canonical_species": name, **rows[name]})


def main(force_refresh: bool = False) -> None:
    if not COORDINATES.exists():
        raise FileNotFoundError(f"Build Phase 5 coordinates first: {COORDINATES}")
    if not SPECIES_SUMMARY.exists():
        raise FileNotFoundError(f"Build Phase 4 species summary first: {SPECIES_SUMMARY}")

    names = sorted(
        pd.read_parquet(COORDINATES, columns=["canonical_species"])["canonical_species"]
        .dropna().astype(str).str.strip().unique(),
        key=str.casefold,
    )
    summary = pd.read_parquet(SPECIES_SUMMARY, columns=["canonical_species", "domain"])
    domains = {
        str(row.canonical_species).strip(): str(row.domain).strip()
        for row in summary.itertuples(index=False)
        if pd.notna(row.domain) and str(row.domain).strip()
    }
    if not names:
        raise ValueError("Coordinate table contains no species names.")

    rows: dict[str, dict[str, str | int | None]] = {}
    if OUTPUT.exists() and not force_refresh:
        previous = pd.read_csv(OUTPUT, keep_default_na=False)
        if set(FIELDS).issubset(previous.columns):
            allowed = set(names)
            for row in previous.to_dict(orient="records"):
                name = str(row["canonical_species"])
                if name in allowed and row["match_status"] in {"found", "not_found"}:
                    rows[name] = {field: row[field] for field in FIELDS[1:]}

    pending = [name for name in names if name not in rows]
    print(f"Matching {len(pending):,} species; reusing {len(rows):,} cached rows.", flush=True)
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        rows.update(request_batch(batch, domains))
        write_table(rows)
        print(f"Matched {min(start + len(batch), len(pending)):,}/{len(pending):,} pending species.", flush=True)
        time.sleep(0.25)

    write_table({name: rows[name] for name in names})
    found = sum(row["match_status"] == "found" for row in rows.values())
    print(f"Wrote {OUTPUT.relative_to(ROOT)}: {found:,}/{len(rows):,} exact species pages found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-refresh", action="store_true", help="Redo all GBIF species match requests.")
    main(force_refresh=parser.parse_args().force_refresh)
