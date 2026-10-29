"""Reproduce V1's first-evidence and publication-growth summaries."""

from __future__ import annotations

import pandas as pd


def build_emergence_table(
    species_summary: pd.DataFrame,
    evidence: pd.DataFrame,
    *,
    first_window_years: int = 10,
    publications_to_reach: int = 10,
    minimum_causation_count: int = 10,
    maximum_article_count: int = 30000,
) -> pd.DataFrame:
    """Build V1-like emergence metrics for species meeting configured bounds."""
    columns = [*species_summary.columns, "year_reaching_n_pubs", "n_years_to_n_pubs", "pubs_in_first_year_window"]
    if evidence.empty or species_summary.empty:
        return pd.DataFrame(columns=columns)
    dated = evidence.copy()
    dated["publication_year"] = pd.to_numeric(dated.get("publication_year"), errors="coerce")
    dated = dated.dropna(subset=["publication_year"])
    if dated.empty:
        return pd.DataFrame(columns=columns)
    dated["publication_year"] = dated["publication_year"].astype(int)
    dated = dated.sort_values(["canonical_species", "publication_year", "pmid"])
    first = dated.groupby("canonical_species")["publication_year"].min().rename("first_evidence_year")
    dated["_cumulative"] = dated.groupby("canonical_species").cumcount() + 1
    reaches = dated[dated["_cumulative"] >= int(publications_to_reach)].groupby("canonical_species")["publication_year"].min()
    reaches.name = "year_reaching_n_pubs"
    joined = species_summary.merge(first, on="canonical_species", how="left", suffixes=("", "_computed"))
    if "first_evidence_year_computed" in joined:
        joined["first_evidence_year"] = joined["first_evidence_year"].fillna(joined.pop("first_evidence_year_computed"))
    joined = joined.merge(reaches, on="canonical_species", how="left")
    window_counts = dated.merge(first.rename("_first"), on="canonical_species")
    window_counts = window_counts[window_counts["publication_year"].between(window_counts["_first"], window_counts["_first"] + int(first_window_years))]
    window_counts = window_counts.groupby("canonical_species").size().rename("pubs_in_first_year_window")
    joined = joined.merge(window_counts, on="canonical_species", how="left")
    joined["n_years_to_n_pubs"] = joined["year_reaching_n_pubs"] - joined["first_evidence_year"]
    eligible = joined["n_causation"].ge(int(minimum_causation_count)) & joined["n_articles"].lt(int(maximum_article_count))
    result = joined.loc[eligible].copy()
    for column in ("first_evidence_year", "year_reaching_n_pubs", "n_years_to_n_pubs"):
        result[column] = result[column].astype("Int64")
    result["pubs_in_first_year_window"] = result["pubs_in_first_year_window"].fillna(0).astype("int64")
    return result.sort_values(["n_years_to_n_pubs", "pubs_in_first_year_window", "canonical_species"], ascending=[True, False, True], na_position="last").reset_index(drop=True)
