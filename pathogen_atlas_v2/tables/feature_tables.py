"""Build PMID-level and species-level tables from domain expert answers."""

from __future__ import annotations

import pandas as pd


def build_feature_tables(answers: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return a tidy answer table and a species-by-feature prevalence table."""
    summary_columns = ["canonical_species", "question_id", "concept", "category", "n_answered", "n_yes", "fraction_yes"]
    if answers.empty:
        return answers.copy(), pd.DataFrame(columns=summary_columns)
    long = answers.copy()
    long["answer_yes"] = long["answer_yes"].astype("boolean")
    scored = long.dropna(subset=["answer_yes"]).copy()
    if scored.empty:
        summary = pd.DataFrame(columns=summary_columns)
    else:
        summary = scored.groupby(["canonical_species", "question_id", "concept", "category"], dropna=False).agg(
            n_answered=("answer_yes", "size"), n_yes=("answer_yes", "sum")
        ).reset_index()
        summary["fraction_yes"] = summary["n_yes"] / summary["n_answered"]
        summary["n_yes"] = summary["n_yes"].astype("int64")
    return long, summary


def build_species_feature_matrix(feature_summary: pd.DataFrame) -> pd.DataFrame:
    """Pivot per-species concept prevalence into a wide, stable matrix."""
    if feature_summary.empty:
        return pd.DataFrame(columns=["canonical_species"])
    matrix = feature_summary.pivot(index="canonical_species", columns="question_id", values="fraction_yes").reset_index()
    matrix.columns.name = None
    matrix = matrix.rename(columns={column: f"feature_{column}" for column in matrix.columns if column != "canonical_species"})
    return matrix.sort_values("canonical_species").reset_index(drop=True)
