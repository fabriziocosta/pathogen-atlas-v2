"""Build PubMed species clauses from the canonical synonym mapping."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from pathogen_atlas_v2.abstracts.pubmed_query_templates import quote_pubmed_term
from pathogen_atlas_v2.species.normalise import normalized_key


PUBMED_SPECIES_FIELDS = {"Abstract", "Title/Abstract", "Keywords", "MeSH Terms"}


@dataclass(frozen=True)
class SpeciesPubMedQuery:
    """Resolved species identity and its exact-name PubMed query clause."""

    input_name: str
    canonical_species: str
    known_names: tuple[str, ...]
    field: str
    query: str


def resolve_species_query_names(
    bacterial_name: str,
    mapping: pd.DataFrame,
) -> tuple[str, tuple[str, ...]]:
    """Resolve a canonical species or synonym to its canonical name and aliases."""
    required = {"input_name", "canonical_species"}
    missing = required - set(mapping.columns)
    if missing:
        raise KeyError(f"Species mapping is missing required columns: {sorted(missing)}")
    key = normalized_key(bacterial_name)
    input_matches = mapping["input_name"].map(normalized_key).eq(key)
    canonical_matches = mapping["canonical_species"].map(normalized_key).eq(key)
    matched = mapping.loc[input_matches | canonical_matches]
    canonical_species = sorted(
        matched["canonical_species"].dropna().astype(str).unique(), key=str.casefold
    )
    if not canonical_species:
        raise KeyError(f"Bacterial name not found in the species mapping: {bacterial_name}")
    if len(canonical_species) > 1:
        raise ValueError(f"Bacterial name maps to multiple canonical species: {canonical_species}")

    canonical = canonical_species[0]
    canonical_key = normalized_key(canonical)
    aliases = mapping.loc[
        mapping["canonical_species"].map(normalized_key).eq(canonical_key), "input_name"
    ].dropna().astype(str)
    names = tuple(sorted({canonical, *aliases.tolist()}, key=str.casefold))
    return canonical, names


def build_species_pubmed_query(
    bacterial_name: str,
    mapping: pd.DataFrame,
    *,
    field: str = "Title/Abstract",
) -> SpeciesPubMedQuery:
    """Build an exact-phrase OR query clause for a species and its synonyms."""
    if field not in PUBMED_SPECIES_FIELDS:
        raise ValueError(f"Unsupported PubMed species field: {field!r}")
    canonical, names = resolve_species_query_names(bacterial_name, mapping)
    clauses = [quote_pubmed_term(name, field) for name in names]
    return SpeciesPubMedQuery(
        input_name=str(bacterial_name),
        canonical_species=canonical,
        known_names=names,
        field=field,
        query="(" + " OR ".join(clauses) + ")",
    )


__all__ = ["PUBMED_SPECIES_FIELDS", "SpeciesPubMedQuery", "build_species_pubmed_query", "resolve_species_query_names"]
