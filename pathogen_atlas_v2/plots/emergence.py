"""Publication-year plots for Phase 4 emergence tables."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def plot_emergence_timeline(emergence: pd.DataFrame, output_path: str | Path, *, top_n: int = 30) -> Path:
    """Plot first evidence year against first year reaching the publication target."""
    import matplotlib.pyplot as plt

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = emergence.copy()
    needed = {"canonical_species", "first_evidence_year", "year_reaching_n_pubs", "n_causation"}
    if frame.empty or needed - set(frame.columns):
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.text(0.5, 0.5, "No emergence records available", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    else:
        frame["first_evidence_year"] = pd.to_numeric(frame["first_evidence_year"], errors="coerce")
        frame["year_reaching_n_pubs"] = pd.to_numeric(frame["year_reaching_n_pubs"], errors="coerce")
        frame["n_causation"] = pd.to_numeric(frame["n_causation"], errors="coerce")
        frame = frame.dropna(subset=["first_evidence_year", "year_reaching_n_pubs"])
        fig, ax = plt.subplots(figsize=(11, 7))
        if frame.empty:
            ax.text(0.5, 0.5, "No species reached the publication target", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
        else:
            points = ax.scatter(frame["first_evidence_year"], frame["year_reaching_n_pubs"], c=frame["n_causation"], cmap="YlOrRd", alpha=0.8, edgecolors="white", linewidths=0.4)
            fig.colorbar(points, ax=ax, label="Accepted human disease evidence abstracts")
            label_rows = frame.nlargest(max(int(top_n), 0), "n_causation")
            for row in label_rows.itertuples(index=False):
                ax.annotate(row.canonical_species, (row.first_evidence_year, row.year_reaching_n_pubs), xytext=(4, 3), textcoords="offset points", fontsize=7)
            lower = min(frame["first_evidence_year"].min(), frame["year_reaching_n_pubs"].min())
            upper = max(frame["first_evidence_year"].max(), frame["year_reaching_n_pubs"].max())
            ax.plot([lower, upper], [lower, upper], linestyle="--", color="0.55", linewidth=1)
            ax.set(xlabel="Year of first accepted evidence", ylabel="Year reaching publication target", title="Pathogen evidence emergence")
            ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output_path
