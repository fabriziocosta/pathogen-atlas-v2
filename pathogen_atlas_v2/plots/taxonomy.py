"""Taxonomic composition plots."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def plot_taxonomy_composition(species: pd.DataFrame, output_path: str | Path, *, rank: str = "phylum", top_n: int = 20) -> Path:
    """Plot accepted-evidence species counts by a taxonomy rank."""
    import matplotlib.pyplot as plt

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if rank not in species:
        raise ValueError(f"Taxonomy table does not have rank {rank!r}.")
    frame = species.loc[pd.to_numeric(species["n_causation"], errors="coerce").fillna(0).gt(0), [rank, "canonical_species"]].copy()
    frame[rank] = frame[rank].fillna("Unclassified").replace("", "Unclassified")
    composition = frame.groupby(rank)["canonical_species"].nunique().sort_values().tail(max(0, int(top_n)))
    fig, ax = plt.subplots(figsize=(9, 6))
    if composition.empty:
        ax.text(0.5, 0.5, "No taxonomy records available", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    else:
        ax.barh(composition.index.astype(str), composition.values, color="#7d78a8")
        ax.set_xlabel("Canonical species with accepted evidence")
        ax.set_title(f"Evidence-bearing species by {rank}")
        ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output_path
