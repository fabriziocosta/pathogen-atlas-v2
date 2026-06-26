"""Normalise LPSN species records into stable tabular fields."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd


RANK_COLUMNS = ["domain", "kingdom", "phylum", "class", "order", "family"]
NORMALISED_COLUMNS = [
    "input_name",
    "canonical_species",
    "mapping_type",
    "input_lpsn_record_id",
    "canonical_lpsn_record_id",
    "status",
    "category",
    "genus",
    "specific_epithet",
    "subspecies_epithet",
    "risk_group",
    "source",
    "source_retrieved_at",
    *RANK_COLUMNS,
]


def normalize_name(name: Any) -> str:
    """Clean a taxon name while preserving scientific-name spelling."""
    if name is None:
        return ""
    text = str(name).replace('"', "").replace("'", "")
    return re.sub(r"\s+", " ", text).strip()


def normalized_key(name: Any) -> str:
    """Return a case-insensitive matching key for a taxon name."""
    return normalize_name(name).casefold()


def get_record_id(record: dict[str, Any]) -> Any:
    """Extract the most likely LPSN record identifier."""
    for key in ("id", "record_no", "lpsn_taxon_id", "lpsn_id"):
        if record.get(key) not in (None, ""):
            return record[key]
    return None


def get_record_name(record: dict[str, Any]) -> str:
    """Extract a display name from an LPSN record."""
    for key in ("full_name", "full_name_html", "taxon_name", "name", "monomial"):
        value = normalize_name(record.get(key))
        if value:
            return value
    genus = normalize_name(record.get("genus_name") or record.get("genus"))
    epithet = normalize_name(record.get("sp_epithet") or record.get("specific_epithet"))
    if genus and epithet:
        return f"{genus} {epithet}"
    return genus


def parse_species_name(name: str) -> tuple[str, str, str]:
    """Split a species or subspecies name into genus, epithet, and subspecies epithet."""
    parts = normalize_name(name).split()
    genus = parts[0] if parts else ""
    specific_epithet = parts[1] if len(parts) > 1 else ""
    subspecies_epithet = ""
    if "subsp." in parts:
        index = parts.index("subsp.")
        if len(parts) > index + 1:
            subspecies_epithet = parts[index + 1]
    elif len(parts) > 2:
        subspecies_epithet = parts[2]
    return genus, specific_epithet, subspecies_epithet


def is_species_record(record: dict[str, Any]) -> bool:
    """Return True for species/subspecies LPSN records."""
    category = str(record.get("category", "")).casefold()
    if category in {"species", "subspecies"}:
        return True
    name = get_record_name(record)
    return len(name.split()) >= 2


def status_text(record: dict[str, Any]) -> str:
    """Extract taxonomic status text."""
    return normalize_name(record.get("lpsn_taxonomic_status") or record.get("status"))


def risk_group(record: dict[str, Any]) -> Any:
    """Extract risk-group metadata if present."""
    for key in ("risk_grp", "risk_group", "biosafety_level"):
        if record.get(key) not in (None, ""):
            return record[key]
    return None


def is_synonym(record: dict[str, Any]) -> bool:
    """Return True if the record is marked as a synonym."""
    return "synonym" in status_text(record).casefold()


def hierarchy_to_ranks(hierarchy: list[dict[str, Any]] | None) -> dict[str, str]:
    """Convert LPSN hierarchy records into fixed rank columns."""
    ranks = {rank: "" for rank in RANK_COLUMNS}
    if not hierarchy:
        return ranks
    for entry in hierarchy:
        category = str(entry.get("category", "")).casefold()
        if category in ranks and not ranks[category]:
            ranks[category] = get_record_name(entry)
    return ranks


def normalise_lpsn_records(
    records: list[dict[str, Any]],
    source: str = "lpsn",
    retrieved_at: str | None = None,
) -> pd.DataFrame:
    """Normalise raw LPSN records into one row per input-name mapping."""
    retrieved_at = retrieved_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows: list[dict[str, Any]] = []
    for record in records:
        if not is_species_record(record):
            continue
        input_name = get_record_name(record)
        if not input_name:
            continue

        accepted_record = record.get("_pathogen_atlas_accepted_record")
        accepted_hierarchy = record.get("_pathogen_atlas_accepted_hierarchy")
        mapping_type = "synonym" if is_synonym(record) else "accepted"
        canonical_record = accepted_record if accepted_record else record
        canonical_name = get_record_name(canonical_record) or input_name
        canonical_id = get_record_id(canonical_record) or record.get("lpsn_correct_name_id") or get_record_id(record)
        if is_synonym(record) and not accepted_record:
            mapping_type = "unresolved_synonym"

        genus, specific_epithet, subspecies_epithet = parse_species_name(canonical_name)
        ranks = hierarchy_to_ranks(accepted_hierarchy or record.get("_pathogen_atlas_hierarchy"))
        rows.append(
            {
                "input_name": input_name,
                "canonical_species": canonical_name,
                "mapping_type": mapping_type,
                "input_lpsn_record_id": get_record_id(record),
                "canonical_lpsn_record_id": canonical_id,
                "status": status_text(record),
                "category": normalize_name(record.get("category")),
                "genus": genus,
                "specific_epithet": specific_epithet,
                "subspecies_epithet": subspecies_epithet,
                "risk_group": risk_group(canonical_record),
                "source": source,
                "source_retrieved_at": retrieved_at,
                **ranks,
            }
        )

    return pd.DataFrame(rows, columns=NORMALISED_COLUMNS)


def main() -> None:
    """Module entry point reserved for direct debugging."""
    raise SystemExit("Use pathogen_atlas_v2.pipelines.01_build_species_synonyms instead.")
