#!/usr/bin/env python3
"""Build the compact, static JSON dataset for the Pathogen Atlas explorer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
RELEASE = ROOT / "data/04_tables/release"
COORDINATES = ROOT / "data/05_plots/release/species_feature_space_coordinates.parquet"
OUTPUT = ROOT / "sites/pathogen-atlas-explorer/dist/explorer-data.json"
EVIDENCE_DIR = ROOT / "sites/pathogen-atlas-explorer/dist/evidence"
TAXONOMY_LEVELS = ("domain", "kingdom", "phylum", "class", "order", "family", "genus")
EVIDENCE_SHARD_PREFIX_LENGTH = 2


def clean_text(value: Any) -> str | None:
    """Convert nullable scalar values to JSON-safe strings."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def as_optional_number(value: Any) -> int | float | None:
    """Convert a nullable pandas scalar to a JSON-safe number."""
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def build_payload(
    evidence: pd.DataFrame,
    species_summary: pd.DataFrame,
    feature_matrix: pd.DataFrame,
    coordinates: pd.DataFrame,
    pathogenic_index: pd.DataFrame,
) -> dict[str, Any]:
    """Join release tables into the small client-side explorer contract."""
    required_coordinates = {"canonical_species", "imste_1", "imste_2"}
    if not required_coordinates.issubset(coordinates.columns):
        raise ValueError(f"Coordinate table needs columns {sorted(required_coordinates)}.")
    required_features = {"canonical_species"}
    if not required_features.issubset(feature_matrix.columns):
        raise ValueError("Feature matrix is missing canonical_species.")
    required_evidence = {"canonical_species", "pmid"}
    if not required_evidence.issubset(evidence.columns):
        raise ValueError("Evidence table needs canonical_species and pmid.")
    if not {"canonical_species", "pathogenic_index"}.issubset(pathogenic_index.columns):
        raise ValueError("Pathogenic-index table needs canonical_species and pathogenic_index.")
    if pathogenic_index["canonical_species"].duplicated().any():
        raise ValueError("Pathogenic-index table must contain exactly one row per species.")
    index_by_species = pathogenic_index.set_index("canonical_species")["pathogenic_index"].to_dict()

    feature_columns = [column for column in feature_matrix.columns if column.startswith("feature_")]
    feature_summary_path = RELEASE / "species_feature_summary.parquet"
    feature_summary = pd.read_parquet(feature_summary_path, columns=["question_id", "concept", "category"])
    features = (
        feature_summary.drop_duplicates("question_id")
        .sort_values(["category", "concept", "question_id"], kind="stable")
        .assign(column=lambda frame: "feature_" + frame["question_id"].astype(str))
    )
    features = features.loc[features["column"].isin(feature_columns)].copy()
    feature_definitions = [
        {
            "id": str(row.question_id),
            "label": str(row.concept),
            "category": str(row.category),
        }
        for row in features.itertuples(index=False)
    ]
    feature_ids = features["question_id"].astype(str).tolist()

    if "accepted" not in evidence.columns:
        raise ValueError("Evidence table needs an 'accepted' flag to identify pathogen-confirming PMIDs.")
    evidence = evidence.loc[evidence["accepted"].fillna(False).astype(bool)].copy()
    evidence["pmid"] = evidence["pmid"].astype(str).str.strip()
    evidence = evidence.loc[evidence["pmid"].str.fullmatch(r"\d+")].drop_duplicates(
        ["canonical_species", "pmid"], keep="last"
    )

    paper_by_species: dict[str, list[dict[str, Any]]] = {}
    paper_columns = [column for column in ("canonical_species", "pmid", "title", "publication_year", "publication_date", "doi") if column in evidence]
    for row in evidence[paper_columns].sort_values(
        [column for column in ("canonical_species", "publication_year", "pmid") if column in paper_columns],
        ascending=[True] + ([False] if "publication_year" in paper_columns else []) + [True],
        na_position="last",
    ).itertuples(index=False, name=None):
        record = dict(zip(paper_columns, row))
        species_name = str(record["canonical_species"])
        paper_by_species.setdefault(species_name, []).append(
            {
                "pmid": str(record["pmid"]),
                "title": clean_text(record.get("title")),
                "year": as_optional_number(record.get("publication_year")),
                "doi": clean_text(record.get("doi")),
            }
        )

    summary_columns = ["canonical_species", *[rank for rank in TAXONOMY_LEVELS if rank in species_summary.columns], "n_articles", "n_sampled", "n_causation"]
    summary_by_species = (
        species_summary[summary_columns]
        .drop_duplicates("canonical_species", keep="last")
        .set_index("canonical_species")
        .to_dict(orient="index")
    )
    feature_by_species = feature_matrix.set_index("canonical_species").to_dict(orient="index")
    coordinate_species = coordinates["canonical_species"].astype(str)
    if coordinate_species.duplicated().any():
        examples = coordinate_species.loc[coordinate_species.duplicated(keep=False)].drop_duplicates().head(5).tolist()
        raise ValueError(f"IMSTE coordinates must contain exactly one row per species; duplicates include {examples}.")
    coordinate_rows = coordinates

    records: list[dict[str, Any]] = []
    for point in coordinate_rows.sort_values("canonical_species").itertuples(index=False):
        name = str(point.canonical_species)
        if name not in index_by_species:
            raise ValueError(f"Pathogenic-index table has no row for plotted species {name!r}.")
        index_value = as_optional_number(index_by_species[name])
        if index_value is None or not 0 <= float(index_value) <= 1:
            raise ValueError(f"Pathogenic index for {name!r} must be a finite value between zero and one.")
        feature_row = feature_by_species.get(name, {})
        summary_row = summary_by_species.get(name, {})
        taxonomy = {rank: clean_text(summary_row.get(rank)) for rank in TAXONOMY_LEVELS if rank in summary_columns}
        shard = hashlib.sha256(name.encode("utf-8")).hexdigest()[:EVIDENCE_SHARD_PREFIX_LENGTH]
        records.append(
            {
                "species": name,
                "x": float(point.imste_1),
                "y": float(point.imste_2),
                "taxonomy": taxonomy,
                "n_articles": as_optional_number(summary_row.get("n_articles")),
                "n_sampled": as_optional_number(summary_row.get("n_sampled")),
                "n_causation": as_optional_number(summary_row.get("n_causation")),
                "n_pmids": len(paper_by_species.get(name, [])),
                "pathogenic_index": float(index_value),
                "evidence_shard": shard,
                "features": [as_optional_number(feature_row.get(f"feature_{question_id}")) for question_id in feature_ids],
            }
        )

    evidence_shards: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for record in records:
        species_name = record["species"]
        shard = record["evidence_shard"]
        evidence_shards.setdefault(shard, {})[species_name] = paper_by_species.get(species_name, [])

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "Pathogen Atlas V2 Phase 4 accepted evidence and Phase 5 IMSTE coordinates",
        "counts": {
            "species": len(records),
            "species_pmid_records": sum(record["n_pmids"] for record in records),
            "features": len(feature_definitions),
            "evidence_shards": len(evidence_shards),
        },
        "taxonomy_levels": list(TAXONOMY_LEVELS),
        "features": feature_definitions,
        "species": records,
        "_evidence_shard_payloads": evidence_shards,
    }


def main() -> None:
    """Load local Phase 4/5 releases and write browser-ready JSON."""
    required_paths = [
        RELEASE / "evidence_records.parquet",
        RELEASE / "species_summary.parquet",
        RELEASE / "pathogenic_index.parquet",
        RELEASE / "species_feature_matrix.parquet",
        RELEASE / "species_feature_summary.parquet",
        COORDINATES,
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Build Phase 4 tables and run Phase 5 IMSTE plots first. Missing: " + ", ".join(missing)
        )
    payload = build_payload(
        pd.read_parquet(RELEASE / "evidence_records.parquet"),
        pd.read_parquet(RELEASE / "species_summary.parquet"),
        pd.read_parquet(RELEASE / "species_feature_matrix.parquet"),
        pd.read_parquet(COORDINATES),
        pd.read_parquet(RELEASE / "pathogenic_index.parquet"),
    )
    evidence_shards = payload.pop("_evidence_shard_payloads")
    for stale_shard in EVIDENCE_DIR.glob("*.json") if EVIDENCE_DIR.exists() else []:
        stale_shard.unlink()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    for shard, species_papers in evidence_shards.items():
        (EVIDENCE_DIR / f"{shard}.json").write_text(
            json.dumps(species_papers, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {OUTPUT.relative_to(ROOT)}: {payload['counts']['species']:,} species, "
        f"{payload['counts']['species_pmid_records']:,} species-PMID records, "
        f"{payload['counts']['features']} features; "
        f"{payload['counts']['evidence_shards']} lazy evidence shards "
        f"({OUTPUT.stat().st_size / 1_000_000:.1f} MB initial dataset)."
    )


if __name__ == "__main__":
    main()
