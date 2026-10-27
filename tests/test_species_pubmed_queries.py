"""Tests for species synonym expansion into PubMed query clauses."""

from __future__ import annotations

import pandas as pd
import pytest

from pathogen_atlas_v2.abstracts.species_queries import (
    build_species_pubmed_query,
    resolve_species_query_names,
)


@pytest.fixture
def mapping() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"input_name": "Granulicatella adiacens", "canonical_species": "Granulicatella adiacens"},
            {"input_name": "Abiotrophia adiacens", "canonical_species": "Granulicatella adiacens"},
        ]
    )


def test_resolve_species_accepts_canonical_or_synonym(mapping: pd.DataFrame) -> None:
    assert resolve_species_query_names("ABiotrophia adiacens", mapping) == (
        "Granulicatella adiacens",
        ("Abiotrophia adiacens", "Granulicatella adiacens"),
    )


def test_build_species_pubmed_query_quotes_all_known_names(mapping: pd.DataFrame) -> None:
    query = build_species_pubmed_query("Abiotrophia adiacens", mapping)
    assert query.canonical_species == "Granulicatella adiacens"
    assert query.known_names == ("Abiotrophia adiacens", "Granulicatella adiacens")
    assert query.query == '("Abiotrophia adiacens"[Title/Abstract] OR "Granulicatella adiacens"[Title/Abstract])'


def test_unknown_or_ambiguous_species_are_reported(mapping: pd.DataFrame) -> None:
    with pytest.raises(KeyError, match="not found"):
        resolve_species_query_names("Unknown bacterium", mapping)

    ambiguous = pd.concat(
        [mapping, pd.DataFrame([{"input_name": "Same name", "canonical_species": "Other species"}])],
        ignore_index=True,
    )
    ambiguous.loc[len(ambiguous)] = {"input_name": "Same name", "canonical_species": "Another species"}
    with pytest.raises(ValueError, match="multiple canonical species"):
        resolve_species_query_names("Same name", ambiguous)


def test_invalid_pubmed_field_is_rejected(mapping: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="Unsupported PubMed species field"):
        build_species_pubmed_query("Granulicatella adiacens", mapping, field="Title/Abstract] OR all")
