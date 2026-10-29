"""Summary plots for the Phase 4 release tables."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _save_empty_or_plot(output_path: Path, title: str, plotter: Any) -> None:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    plotter(ax)
    ax.set_title(title)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_species_evidence_counts(species: pd.DataFrame, output_path: str | Path, *, top_n: int = 30) -> Path:
    """Plot the species with the most accepted abstracts."""
    path = Path(output_path)
    scored = species.loc[pd.to_numeric(species["n_causation"], errors="coerce").fillna(0).gt(0)]
    frame = scored.sort_values(["n_causation", "canonical_species"], ascending=[True, True]).tail(max(0, int(top_n)))
    def draw(ax):
        if frame.empty:
            ax.text(0.5, 0.5, "No evidence records available", ha="center", va="center", transform=ax.transAxes)
            return
        ax.barh(frame["canonical_species"], frame["n_causation"], color="#b34b4b")
        ax.set_xlabel("Accepted human disease evidence abstracts")
        ax.grid(axis="x", alpha=0.2)
    _save_empty_or_plot(path, "Species with most accepted evidence", draw)
    return path


def plot_feature_prevalence(features: pd.DataFrame, output_path: str | Path, *, top_n: int = 30) -> Path:
    """Plot overall prevalence of expert-question concepts across species–abstract rows."""
    path = Path(output_path)
    if features.empty:
        aggregate = pd.DataFrame(columns=["concept", "fraction_yes"])
    else:
        aggregate = features.groupby(["question_id", "concept", "category"], dropna=False).agg(n_yes=("n_yes", "sum"), n_answered=("n_answered", "sum")).reset_index()
        aggregate["fraction_yes"] = aggregate["n_yes"] / aggregate["n_answered"].replace(0, pd.NA)
        aggregate = aggregate.sort_values(["fraction_yes", "concept"], ascending=[True, True]).tail(max(0, int(top_n)))
    def draw(ax):
        if aggregate.empty:
            ax.text(0.5, 0.5, "No expert-question answers available", ha="center", va="center", transform=ax.transAxes)
            return
        ax.barh(aggregate["concept"].astype(str), aggregate["fraction_yes"], color="#4c8c73")
        ax.set_xlim(0, 1)
        ax.set_xlabel("Fraction of answered abstracts with yes")
        ax.grid(axis="x", alpha=0.2)
    _save_empty_or_plot(path, "Most frequently reported pathogen features", draw)
    return path


def plot_annual_evidence_counts(evidence: pd.DataFrame, output_path: str | Path) -> Path:
    """Plot accepted evidence abstracts by publication year."""
    path = Path(output_path)
    frame = evidence.copy()
    if not frame.empty and "publication_year" in frame:
        frame["publication_year"] = pd.to_numeric(frame["publication_year"], errors="coerce")
        frame = frame.dropna(subset=["publication_year"])
        yearly = frame.groupby("publication_year")["pmid"].nunique().sort_index()
    else:
        yearly = pd.Series(dtype="int64")
    def draw(ax):
        if yearly.empty:
            ax.text(0.5, 0.5, "No dated evidence records available", ha="center", va="center", transform=ax.transAxes)
            return
        ax.plot(yearly.index, yearly.values, color="#3f668e", linewidth=2)
        ax.fill_between(yearly.index, yearly.values, alpha=0.15, color="#3f668e")
        ax.set_xlabel("Publication year")
        ax.set_ylabel("Unique accepted PMID count")
        ax.grid(alpha=0.2)
    _save_empty_or_plot(path, "Human disease evidence by publication year", draw)
    return path
