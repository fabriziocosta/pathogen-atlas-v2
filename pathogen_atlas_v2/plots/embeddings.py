"""IMSTE visualization of species-level domain-expert feature profiles."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def plot_species_feature_space(
    feature_matrix: pd.DataFrame,
    output_path: str | Path,
    *,
    max_labels: int = 8,
    feature_panels: int = 3,
    n_msts: int = 15,
    n_epochs: int = 300,
    random_state: int = 42,
    mst_method: str = "famst",
    mst_neighbors: int = 15,
    mst_inter_component_edges: int = 5,
    mst_max_neighbors: int | None = None,
    negative_ratio: int = 5,
    lambda_rep: float = 0.5,
    learning_rate: float = 0.05,
    batch_size: int = 32768,
    epsilon: float = 1e-4,
    logistic_margin: float = 1.0,
    logistic_temperature: float = 0.5,
    mst_representatives_per_component: int = 10,
    device: str = "auto",
) -> Path:
    """Embed species by their domain-expert feature profiles with IMSTE.

    Each point is a species and the input vector contains its per-question
    prevalence of ``yes`` answers. Missing feature values are imputed to that
    feature's across-species mean, then features are standardized before IMSTE
    builds the ranked minimum-spanning-tree embedding. The panels color the
    same coordinates by the most variable feature prevalences.
    """
    import matplotlib.pyplot as plt
    from sklearn.preprocessing import StandardScaler

    try:
        from imste import IteratedMinimumSpanningTreeEmbedder
    except ImportError as exc:
        raise RuntimeError(
            "IMSTE is required for the species feature-space plot. Install the "
            "plotting extra with `python -m pip install -e '.[viz]'`."
        ) from exc

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    feature_columns = [column for column in feature_matrix.columns if column != "canonical_species"]
    species = feature_matrix.get(
        "canonical_species", pd.Series(dtype=str)
    ).astype(str).tolist()
    if len(species) < 2 or not feature_columns:
        fig, ax = plt.subplots(figsize=(9, 6))
        message = "At least two species and one expert feature are needed for IMSTE."
        if len(species) == 1:
            message = f"Only one species has a feature profile: {species[0]}"
        ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(output_path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        return output_path

    numeric = feature_matrix[feature_columns].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.dropna(axis=1, how="all")
    if numeric.empty:
        fig, ax = plt.subplots(figsize=(9, 6))
        ax.text(0.5, 0.5, "No scored domain-expert features are available.", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(output_path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        return output_path

    observed_features = numeric.copy()
    numeric = numeric.fillna(numeric.mean(axis=0))
    variances = numeric.var(axis=0, ddof=0)
    variable = variances[variances > 0]
    if variable.empty:
        fig, ax = plt.subplots(figsize=(9, 6))
        ax.text(0.5, 0.5, "Species have identical domain-expert feature profiles.", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(output_path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        return output_path

    values = StandardScaler().fit_transform(numeric[variable.index].to_numpy(dtype=float))
    neighbors = min(max(1, int(mst_neighbors)), len(species) - 1)
    max_neighbors = (
        None
        if mst_max_neighbors is None
        else max(neighbors, min(int(mst_max_neighbors), len(species) - 1))
    )
    mapper = IteratedMinimumSpanningTreeEmbedder(
        n_msts=int(n_msts),
        n_epochs=int(n_epochs),
        mst_method=str(mst_method),
        mst_neighbors=max(1, neighbors),
        mst_inter_component_edges=int(mst_inter_component_edges),
        mst_max_neighbors=max_neighbors,
        negative_ratio=int(negative_ratio),
        lambda_rep=float(lambda_rep),
        learning_rate=float(learning_rate),
        batch_size=int(batch_size),
        epsilon=float(epsilon),
        logistic_margin=float(logistic_margin),
        logistic_temperature=float(logistic_temperature),
        mst_representatives_per_component=int(mst_representatives_per_component),
        random_state=int(random_state),
        device=str(device),
    )
    coordinates = mapper.fit_transform(values)
    coordinate_table = pd.DataFrame(
        {
            "canonical_species": species,
            "imste_1": coordinates[:, 0],
            "imste_2": coordinates[:, 1],
        }
    )
    coordinate_path = output_path.with_name("species_feature_space_coordinates.parquet")
    coordinate_table.to_parquet(coordinate_path, index=False)

    panel_count = min(max(1, int(feature_panels)), len(variable))
    selected_features = variable.sort_values(ascending=False).head(panel_count).index.tolist()
    fig, axes = plt.subplots(
        1,
        panel_count,
        figsize=(6.2 * panel_count, 5.8),
        squeeze=False,
    )
    axes = axes.ravel()
    centered = coordinates - coordinates.mean(axis=0, keepdims=True)
    label_count = min(len(species), max(0, int(max_labels)))
    label_indices: list[int] = []
    if label_count:
        label_indices.append(int(np.argmax(np.linalg.norm(centered, axis=1))))
        min_distances = np.linalg.norm(coordinates - coordinates[label_indices[0]], axis=1)
        while len(label_indices) < label_count:
            candidates = min_distances.copy()
            candidates[label_indices] = -1
            next_index = int(np.argmax(candidates))
            label_indices.append(next_index)
            distances = np.linalg.norm(coordinates - coordinates[next_index], axis=1)
            min_distances = np.minimum(min_distances, distances)

    for index, (axis, feature) in enumerate(zip(axes, selected_features)):
        values_for_color = observed_features[feature].to_numpy(dtype=float)
        observed_mask = np.isfinite(values_for_color)
        points = axis.scatter(
            coordinates[observed_mask, 0],
            coordinates[observed_mask, 1],
            c=values_for_color[observed_mask],
            cmap="viridis",
            vmin=0,
            vmax=1,
            s=44,
            alpha=0.88,
            edgecolors="white",
            linewidths=0.35,
        )
        if (~observed_mask).any():
            axis.scatter(
                coordinates[~observed_mask, 0],
                coordinates[~observed_mask, 1],
                color="0.78",
                s=44,
                alpha=0.9,
                edgecolors="white",
                linewidths=0.35,
                label="Not scored",
            )
            if index == 0:
                axis.legend(loc="best", frameon=False, fontsize=8)
        if index == 0:
            for row in label_indices:
                axis.annotate(
                    species[row],
                    coordinates[row],
                    xytext=(3, 3),
                    textcoords="offset points",
                    fontsize=6,
                )
        readable_feature = str(feature).removeprefix("feature_").replace("_", " ").title()
        axis.set_title(readable_feature)
        axis.set_xlabel("IMSTE 1")
        axis.set_ylabel("IMSTE 2")
        axis.set_aspect("equal", adjustable="datalim")
        axis.grid(alpha=0.15)
        fig.colorbar(points, ax=axis, label="Fraction of abstracts scored yes", shrink=0.82)

    fig.suptitle("IMSTE map of species by domain-expert feature profiles", y=1.02)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)

    metadata = {
        "embedding": "IMSTE",
        "estimator": "IteratedMinimumSpanningTreeEmbedder",
        "n_msts": int(n_msts),
        "n_epochs": int(n_epochs),
        "random_state": int(random_state),
        "mst_method": str(mst_method),
        "mst_neighbors": int(neighbors),
        "mst_inter_component_edges": int(mst_inter_component_edges),
        "mst_max_neighbors": max_neighbors,
        "negative_ratio": int(negative_ratio),
        "lambda_rep": float(lambda_rep),
        "learning_rate": float(learning_rate),
        "batch_size": int(batch_size),
        "epsilon": float(epsilon),
        "logistic_margin": float(logistic_margin),
        "logistic_temperature": float(logistic_temperature),
        "mst_representatives_per_component": int(mst_representatives_per_component),
        "device": str(device),
        "n_species": len(species),
        "n_features": len(variable),
        "coordinates_path": str(coordinate_path),
        "feature_columns_used": [str(column) for column in variable.index],
        "feature_panels": [str(column) for column in selected_features],
        "missing_values": "imputed to the feature mean, then standardized",
    }
    output_path.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return output_path
