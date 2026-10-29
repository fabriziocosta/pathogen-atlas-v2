"""Build species-level summaries from Phase 2 and Phase 3 releases."""

from __future__ import annotations

import pandas as pd


def build_species_summary(
    articles: pd.DataFrame,
    matches: pd.DataFrame,
    evidence: pd.DataFrame,
    canonical_pmid_index: pd.DataFrame | None = None,
    sampled_pmid_index: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Summarize full PubMed search counts and accepted evidence per canonical species.

    ``canonical_pmid_index`` provides the number of unique articles from the
    complete query results; fetched abstracts are intentionally just a sample.
    """
    selected = matches.copy()
    if "selected_for_fetch" in selected:
        selected = selected[selected["selected_for_fetch"].fillna(False).astype(bool)]
    selected["pmid"] = selected["pmid"].astype(str)
    selected = selected.drop_duplicates(["pmid", "canonical_species"])
    selected = selected[selected["pmid"].isin(set(articles["pmid"].astype(str)))]
    sampled_counts = selected.groupby("canonical_species")["pmid"].nunique().rename("n_sampled")
    article_counts = sampled_counts.rename("n_articles")
    # Phase 2's two indexes preserve full PubMed hits and the intended sample
    # size independently from the number of abstracts successfully fetched.
    if canonical_pmid_index is not None and {"canonical_species", "n_pmids"}.issubset(canonical_pmid_index.columns):
        full_counts = canonical_pmid_index[["canonical_species", "n_pmids"]].drop_duplicates("canonical_species").set_index("canonical_species")["n_pmids"]
        article_counts = full_counts.rename("n_articles")
    if sampled_pmid_index is not None and {"canonical_species", "n_pmids"}.issubset(sampled_pmid_index.columns):
        sample_counts = sampled_pmid_index[["canonical_species", "n_pmids"]].drop_duplicates("canonical_species").set_index("canonical_species")["n_pmids"]
        sampled_counts = sample_counts.rename("n_sampled")
    if evidence.empty:
        causal_counts = pd.Series(dtype="int64", name="n_causation")
        first_years = pd.Series(dtype="float64", name="first_evidence_year")
    else:
        causal_counts = evidence.groupby("canonical_species")["pmid"].nunique().rename("n_causation")
        dated = evidence.assign(_year=pd.to_numeric(evidence.get("publication_year"), errors="coerce")).dropna(subset=["_year"])
        first_years = dated.groupby("canonical_species")["_year"].min().rename("first_evidence_year")
    result = pd.concat([article_counts, sampled_counts, causal_counts, first_years], axis=1).fillna({"n_articles": 0, "n_sampled": 0, "n_causation": 0})
    result["n_articles"] = result["n_articles"].astype("int64")
    result["n_sampled"] = result["n_sampled"].astype("int64")
    result["n_causation"] = result["n_causation"].astype("int64")
    result["first_evidence_year"] = result["first_evidence_year"].astype("Int64")
    return result.reset_index().sort_values("canonical_species").reset_index(drop=True)
