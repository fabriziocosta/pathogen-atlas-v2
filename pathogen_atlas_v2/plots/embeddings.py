"""Two-dimensional species feature-profile projection."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def plot_species_feature_space(feature_matrix: pd.DataFrame, output_path: str | Path, *, max_labels: int = 40) -> Path:
    """Project 73-question species feature profiles to two PCA axes and plot.

    This provides a reproducible, explainable V2 analogue of V1's species
    one-hot/anonymised embedding overview; it does not make cluster labels.
    """
    import matplotlib.pyplot as plt

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    feature_columns = [column for column in feature_matrix.columns if column != "canonical_species"]
    fig, ax = plt.subplots(figsize=(10, 7))
    if feature_matrix.empty or not feature_columns:
        ax.text(0.5, 0.5, "No species feature profiles available", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    else:
        values = feature_matrix[feature_columns].apply(pd.to_numeric, errors="coerce").fillna(0).to_numpy(dtype=float)
        centered = values - values.mean(axis=0, keepdims=True)
        if centered.shape[0] == 1:
            coordinates = np.zeros((1, 2))
            explained = np.array([0.0, 0.0])
        else:
            _, singular_values, right_vectors = np.linalg.svd(centered, full_matrices=False)
            count = min(2, right_vectors.shape[0])
            coordinates = centered @ right_vectors[:count].T
            if count == 1:
                coordinates = np.column_stack([coordinates[:, 0], np.zeros(len(coordinates))])
            variance = singular_values ** 2
            explained = variance[:2] / variance.sum() if variance.sum() else np.zeros(2)
            if len(explained) == 1:
                explained = np.array([explained[0], 0.0])
        ax.scatter(coordinates[:, 0], coordinates[:, 1], color="#416c8c", alpha=0.75, edgecolors="white", linewidths=0.3)
        if len(coordinates) <= max_labels:
            label_indices = np.arange(len(coordinates))
        else:
            label_indices = np.argsort(np.linalg.norm(coordinates, axis=1))[-max(0, int(max_labels)):]
        names = feature_matrix["canonical_species"].astype(str).tolist()
        for index in label_indices:
            ax.annotate(names[index], coordinates[index], xytext=(3, 3), textcoords="offset points", fontsize=6)
        ax.set_xlabel(f"Feature profile PC1 ({explained[0]:.1%} variance)")
        ax.set_ylabel(f"Feature profile PC2 ({explained[1]:.1%} variance)")
        ax.set_title("Species similarity across domain expert features")
        ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output_path
