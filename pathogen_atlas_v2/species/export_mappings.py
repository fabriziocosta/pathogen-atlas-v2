"""Export Phase 1 species mappings and metadata."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any

import pandas as pd

from pathogen_atlas_v2.common.io import ensure_parent, write_json
from pathogen_atlas_v2.species.acquire import (
    LpsnSourceConfig,
    acquire_lpsn_species_records,
    load_species_source_config,
    utc_now_iso,
)
from pathogen_atlas_v2.species.normalise import RANK_COLUMNS, normalise_lpsn_records
from pathogen_atlas_v2.species.synonyms import build_synonym_mapping


CANONICAL_COLUMNS = [
    "canonical_species",
    "genus",
    "specific_epithet",
    "subspecies_epithet",
    "lpsn_record_id",
    "status",
    "risk_group",
    "source",
    "source_retrieved_at",
]
TAXONOMY_COLUMNS = ["canonical_species", *RANK_COLUMNS, "genus", "source"]


def build_species_canonical(normalised_df: pd.DataFrame) -> pd.DataFrame:
    """Build one canonical-species row per accepted species name."""
    columns = {
        "canonical_species": normalised_df["canonical_species"],
        "genus": normalised_df["genus"],
        "specific_epithet": normalised_df["specific_epithet"],
        "subspecies_epithet": normalised_df["subspecies_epithet"],
        "lpsn_record_id": normalised_df["canonical_lpsn_record_id"],
        "status": normalised_df["status"],
        "risk_group": normalised_df["risk_group"],
        "source": normalised_df["source"],
        "source_retrieved_at": normalised_df["source_retrieved_at"],
    }
    canonical_df = pd.DataFrame(columns)
    canonical_df = canonical_df[canonical_df["canonical_species"].notna() & (canonical_df["canonical_species"] != "")]
    return canonical_df.drop_duplicates(subset=["canonical_species"]).sort_values("canonical_species").reset_index(drop=True)


def build_species_taxonomy(normalised_df: pd.DataFrame) -> pd.DataFrame:
    """Build one taxonomy row per canonical species."""
    taxonomy_df = normalised_df[TAXONOMY_COLUMNS].copy()
    taxonomy_df = taxonomy_df[taxonomy_df["canonical_species"].notna() & (taxonomy_df["canonical_species"] != "")]
    return taxonomy_df.drop_duplicates(subset=["canonical_species"]).sort_values("canonical_species").reset_index(drop=True)


def compare_v1_references(mapping_df: pd.DataFrame, v1_reference: dict[str, Path]) -> dict[str, Any]:
    """Compare V2 outputs with V1 reference files when they are available."""
    summary: dict[str, Any] = {"available": False, "files": {}}
    mapping_path = v1_reference.get("species_to_canonical_path")
    synonym_path = v1_reference.get("bacteria_synonym_lookup_path")
    species_path = v1_reference.get("pathogen_atlas_species_path")
    taxonomy_path = v1_reference.get("taxonomy_path")

    for label, path in {
        "species_to_canonical": mapping_path,
        "bacteria_synonym_lookup": synonym_path,
        "pathogen_atlas_species": species_path,
        "taxonomy": taxonomy_path,
    }.items():
        summary["files"][label] = {"path": str(path) if path else None, "exists": bool(path and path.exists())}

    if not mapping_path or not mapping_path.exists():
        summary["reason"] = "V1 species_to_canonical reference not found."
        return summary

    v1_mapping = pd.read_csv(mapping_path)
    summary["available"] = True
    summary["v1_rows"] = int(len(v1_mapping))
    summary["v1_duplicate_rows"] = int(v1_mapping.duplicated().sum())
    if {"Species", "CanonicalSpecies"}.issubset(v1_mapping.columns):
        conflicts = v1_mapping.groupby("Species")["CanonicalSpecies"].nunique(dropna=True)
        summary["v1_conflicting_species"] = int((conflicts > 1).sum())
        v2_pairs = set(zip(mapping_df["input_name"], mapping_df["canonical_species"]))
        v1_pairs = set(zip(v1_mapping["Species"], v1_mapping["CanonicalSpecies"]))
        summary["shared_pairs"] = int(len(v1_pairs & v2_pairs))
        summary["v1_pairs_not_in_v2"] = int(len(v1_pairs - v2_pairs))
        summary["v2_pairs_not_in_v1"] = int(len(v2_pairs - v1_pairs))
    return summary


def package_versions() -> dict[str, str | None]:
    """Return package versions relevant to Phase 1."""
    versions: dict[str, str | None] = {}
    for package in ("lpsn", "pandas", "pyarrow", "yaml"):
        try:
            versions[package] = importlib.metadata.version("PyYAML" if package == "yaml" else package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def write_release_outputs(
    config: LpsnSourceConfig,
    normalised_df: pd.DataFrame,
    raw_record_count: int,
    retrieved_at: str,
) -> dict[str, Any]:
    """Write Phase 1 release parquet files and metadata JSON."""
    mapping_df, mapping_diagnostics = build_synonym_mapping(normalised_df)
    canonical_df = build_species_canonical(normalised_df)
    taxonomy_df = build_species_taxonomy(normalised_df)

    ensure_parent(config.normalised_records_path)
    normalised_df.to_parquet(config.normalised_records_path, index=False)
    ensure_parent(config.release["species_canonical_path"])
    ensure_parent(config.release["species_synonym_mapping_path"])
    ensure_parent(config.release["species_taxonomy_path"])
    canonical_df.to_parquet(config.release["species_canonical_path"], index=False)
    mapping_df.to_parquet(config.release["species_synonym_mapping_path"], index=False)
    taxonomy_df.to_parquet(config.release["species_taxonomy_path"], index=False)

    metadata: dict[str, Any] = {
        "source": config.name,
        "source_retrieved_at": retrieved_at,
        "package_versions": package_versions(),
        "record_counts": {
            "raw_records": int(raw_record_count),
            "normalised_rows": int(len(normalised_df)),
            "canonical_species": int(len(canonical_df)),
            "synonym_mappings": int(len(mapping_df)),
            "taxonomy_rows": int(len(taxonomy_df)),
        },
        "mapping_diagnostics": mapping_diagnostics.to_dict(),
        "v1_comparison": compare_v1_references(mapping_df, config.v1_reference),
        "outputs": {
            "normalised_records_path": str(config.normalised_records_path),
            **{key: str(path) for key, path in config.release.items()},
        },
    }
    write_json(config.release["metadata_path"], metadata)
    return metadata


def run_phase1(config_path: str | Path = "configs/species_sources.yaml", force_refresh: bool = False) -> dict[str, Any]:
    """Run the complete Phase 1 species synonym rebuild."""
    config = load_species_source_config(config_path)
    retrieved_at = utc_now_iso()
    records = acquire_lpsn_species_records(config=config, force_refresh=force_refresh)
    normalised_df = normalise_lpsn_records(records, source=config.name, retrieved_at=retrieved_at)
    return write_release_outputs(
        config=config,
        normalised_df=normalised_df,
        raw_record_count=len(records),
        retrieved_at=retrieved_at,
    )


def main() -> None:
    """Run the complete Phase 1 export."""
    run_phase1()
