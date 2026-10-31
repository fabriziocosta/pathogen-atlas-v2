"""Compute the V1 sampling-adjusted pathogenic index per species."""

from __future__ import annotations

import numpy as np
import pandas as pd


INDEX_INPUT_COLUMNS = ("canonical_species", "n_causation", "n_articles", "n_sampled")


def build_pathogenic_index(species_summary: pd.DataFrame) -> pd.DataFrame:
    """Estimate a normalized pathogenic index using the V1 formula.

    V1 estimated total causative abstracts as
    ``n_causation / n_sampled * n_articles``, took its natural logarithm, and
    divided by the largest log estimate. Species with no accepted abstracts
    or no sample receive an index of zero in V2, where the summary also
    includes species with no positive pathogen decisions.
    """
    missing = set(INDEX_INPUT_COLUMNS) - set(species_summary.columns)
    if missing:
        raise ValueError(f"Species summary is missing pathogenic-index fields: {sorted(missing)}")

    frame = species_summary[list(INDEX_INPUT_COLUMNS)].copy()
    if frame["canonical_species"].duplicated().any():
        duplicates = frame.loc[frame["canonical_species"].duplicated(keep=False), "canonical_species"].drop_duplicates().head(5).tolist()
        raise ValueError(f"Species summary must have one row per canonical species; duplicates include {duplicates}.")

    for column in ("n_causation", "n_articles", "n_sampled"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
        if frame[column].lt(0).any():
            raise ValueError(f"{column} cannot contain negative values.")
    if frame["n_causation"].gt(frame["n_sampled"]).any():
        raise ValueError("n_causation cannot exceed n_sampled.")
    if frame["n_sampled"].gt(frame["n_articles"]).any():
        raise ValueError("n_sampled cannot exceed n_articles.")

    eligible = frame["n_sampled"].gt(0) & frame["n_articles"].gt(0) & frame["n_causation"].gt(0)
    frame["estimated_causation"] = 0.0
    frame.loc[eligible, "estimated_causation"] = (
        frame.loc[eligible, "n_causation"]
        / frame.loc[eligible, "n_sampled"]
        * frame.loc[eligible, "n_articles"]
    )
    frame["log_estimated_causation"] = 0.0
    frame.loc[eligible, "log_estimated_causation"] = np.log(frame.loc[eligible, "estimated_causation"])

    maximum_log = float(frame.loc[eligible, "log_estimated_causation"].max()) if eligible.any() else 0.0
    frame["pathogenic_index"] = 0.0
    if maximum_log > 0:
        frame.loc[eligible, "pathogenic_index"] = frame.loc[eligible, "log_estimated_causation"] / maximum_log

    frame["n_causation"] = frame["n_causation"].astype("int64")
    frame["n_articles"] = frame["n_articles"].astype("int64")
    frame["n_sampled"] = frame["n_sampled"].astype("int64")
    return frame.sort_values("canonical_species").reset_index(drop=True)
