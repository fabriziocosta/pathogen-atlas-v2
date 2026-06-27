"""Build and validate species synonym mappings."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from pathogen_atlas_v2.species.normalise import normalized_key


SYNONYM_MAPPING_COLUMNS = [
    "input_name",
    "canonical_species",
    "mapping_type",
    "input_lpsn_record_id",
    "canonical_lpsn_record_id",
    "status",
    "source",
]
SYNONYM_CONFLICT_COLUMNS = [*SYNONYM_MAPPING_COLUMNS, "input_key", "conflicting_canonical_species_count"]


@dataclass(frozen=True)
class MappingDiagnostics:
    """Diagnostics for synonym mapping quality."""

    duplicate_rows: int
    conflicting_input_names: int
    null_canonical_species: int

    def to_dict(self) -> dict[str, int]:
        """Return JSON-serialisable diagnostics."""
        return {
            "duplicate_rows": self.duplicate_rows,
            "conflicting_input_names": self.conflicting_input_names,
            "null_canonical_species": self.null_canonical_species,
        }


def detect_mapping_diagnostics(mapping_df: pd.DataFrame) -> MappingDiagnostics:
    """Detect duplicates, conflicts, and null canonical names."""
    if mapping_df.empty:
        return MappingDiagnostics(duplicate_rows=0, conflicting_input_names=0, null_canonical_species=0)
    work = mapping_df.copy()
    work["input_key"] = work["input_name"].map(normalized_key)
    conflicts = work.groupby("input_key")["canonical_species"].nunique(dropna=True)
    return MappingDiagnostics(
        duplicate_rows=int(work.duplicated().sum()),
        conflicting_input_names=int((conflicts > 1).sum()),
        null_canonical_species=int(work["canonical_species"].isna().sum() + (work["canonical_species"] == "").sum()),
    )


def split_conflicting_mappings(mapping_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split unambiguous release mappings from conflicting diagnostic mappings."""
    if mapping_df.empty:
        return mapping_df.copy(), pd.DataFrame(columns=SYNONYM_CONFLICT_COLUMNS)

    work = mapping_df.copy()
    work["input_key"] = work["input_name"].map(normalized_key)
    canonical_counts = work.groupby("input_key")["canonical_species"].nunique(dropna=True)
    conflicting_keys = set(canonical_counts[canonical_counts > 1].index)
    if not conflicting_keys:
        return mapping_df.copy(), pd.DataFrame(columns=SYNONYM_CONFLICT_COLUMNS)

    conflicts_df = work[work["input_key"].isin(conflicting_keys)].copy()
    conflicts_df["conflicting_canonical_species_count"] = conflicts_df["input_key"].map(canonical_counts).astype("int64")
    conflicts_df = conflicts_df[SYNONYM_CONFLICT_COLUMNS].sort_values(
        ["input_key", "canonical_species", "input_name"]
    ).reset_index(drop=True)

    release_df = mapping_df[~work["input_key"].isin(conflicting_keys)].copy().reset_index(drop=True)
    return release_df, conflicts_df


def build_synonym_mapping(normalised_df: pd.DataFrame) -> tuple[pd.DataFrame, MappingDiagnostics, pd.DataFrame]:
    """Build a stable input-name to canonical-species mapping plus diagnostics.

    The release mapping excludes ambiguous input names and rows without a canonical
    species. Excluded ambiguous rows are returned as a diagnostics table.
    """
    missing = [column for column in SYNONYM_MAPPING_COLUMNS if column not in normalised_df.columns]
    if missing:
        raise ValueError(f"Normalised species records are missing required columns: {missing}")
    mapping_df = normalised_df[SYNONYM_MAPPING_COLUMNS].copy()
    mapping_df = mapping_df.drop_duplicates().sort_values(["input_name", "canonical_species"]).reset_index(drop=True)
    diagnostics = detect_mapping_diagnostics(mapping_df)
    release_df, conflicts_df = split_conflicting_mappings(mapping_df)
    release_df = release_df[
        release_df["canonical_species"].notna() & (release_df["canonical_species"] != "")
    ].reset_index(drop=True)
    return release_df, diagnostics, conflicts_df


class SynonymLookup:
    """In-memory lookup for canonical species and synonyms."""

    def __init__(self, mapping_df: pd.DataFrame):
        mapping_df, diagnostics, _ = build_synonym_mapping(mapping_df)
        self.diagnostics = diagnostics
        self._input_to_canonical = {
            normalized_key(row.input_name): row.canonical_species
            for row in mapping_df.itertuples(index=False)
        }
        self._canonical_to_synonyms: dict[str, list[str]] = {}
        for row in mapping_df.itertuples(index=False):
            self._canonical_to_synonyms.setdefault(row.canonical_species, []).append(row.input_name)
        self._canonical_to_synonyms = {
            canonical: sorted(set(names), key=str.casefold)
            for canonical, names in self._canonical_to_synonyms.items()
        }

    def canonical_name(self, name: str) -> str:
        """Return the canonical species for an input synonym or accepted name."""
        key = normalized_key(name)
        if key not in self._input_to_canonical:
            raise KeyError(f"Unknown species name: {name}")
        return self._input_to_canonical[key]

    def synonyms_for(self, canonical_species: str) -> tuple[str, ...]:
        """Return known input names for a canonical species."""
        return tuple(self._canonical_to_synonyms.get(canonical_species, ()))


def main() -> None:
    """Module entry point reserved for direct debugging."""
    raise SystemExit("Use pathogen_atlas_v2.pipelines.01_build_species_synonyms instead.")
