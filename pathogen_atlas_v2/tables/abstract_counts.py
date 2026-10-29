"""Build publication-year abstract count tables from accepted evidence."""

from __future__ import annotations

import pandas as pd


def build_abstract_count_table(evidence: pd.DataFrame) -> pd.DataFrame:
    """Count unique evidence abstracts by canonical species and publication year."""
    columns = ["canonical_species", "publication_year", "n_abstracts"]
    if evidence.empty or "publication_year" not in evidence:
        return pd.DataFrame(columns=columns)
    frame = evidence[["canonical_species", "publication_year", "pmid"]].copy()
    frame["publication_year"] = pd.to_numeric(frame["publication_year"], errors="coerce").astype("Int64")
    frame = frame.dropna(subset=["publication_year"])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    return frame.groupby(["canonical_species", "publication_year"], as_index=False).agg(n_abstracts=("pmid", "nunique")).sort_values(["canonical_species", "publication_year"]).reset_index(drop=True)
