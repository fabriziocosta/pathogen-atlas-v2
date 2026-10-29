"""Phase 5: generate figures from the Phase 4 release tables."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from pathogen_atlas_v2.common.io import ensure_parent, load_yaml, resolve_path
from pathogen_atlas_v2.notebook_utils import notebook_phase_parameters
from pathogen_atlas_v2.plots.dashboards import plot_annual_evidence_counts, plot_feature_prevalence, plot_species_evidence_counts
from pathogen_atlas_v2.plots.emergence import plot_emergence_timeline
from pathogen_atlas_v2.plots.embeddings import plot_species_feature_space
from pathogen_atlas_v2.plots.taxonomy import plot_taxonomy_composition


def phase5_preflight(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report whether all required Phase 4 tables are available."""
    settings = dict(config or {})
    release_dir = resolve_path(str(settings.get("release_dir", "data/04_tables/release")))
    names = ("evidence_records", "species_summary", "species_feature_summary", "species_feature_matrix", "emergence")
    paths = {name: release_dir / f"{name}.parquet" for name in names}
    missing = [str(path) for path in paths.values() if not path.exists()]
    return {"status": "not_ready" if missing else "ready", "inputs": {name: {"path": str(path), "exists": path.exists()} for name, path in paths.items()}, "missing_inputs": missing}


def build_phase5_plots(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load Phase 4 outputs and generate reproducible PNG figures."""
    settings = dict(config or {})
    preflight = phase5_preflight(settings)
    if preflight["status"] != "ready":
        return preflight
    release_dir = resolve_path(str(settings.get("release_dir", "data/04_tables/release")))
    plot_dir = resolve_path(str(settings.get("plot_dir", "data/05_plots/release")))
    plot_dir.mkdir(parents=True, exist_ok=True)
    tables = {name: pd.read_parquet(release_dir / f"{name}.parquet") for name in ("evidence_records", "species_summary", "species_feature_summary", "species_feature_matrix", "emergence")}
    plot_specs_path = resolve_path(str(settings.get("plot_specs_path", "configs/plot_specs.yaml")))
    specifications = load_yaml(plot_specs_path).get("plots", [])
    plotters = {
        "species_evidence_counts": lambda output: plot_species_evidence_counts(tables["species_summary"], output, top_n=int(settings.get("top_n_species", 30))),
        "emergence_timeline": lambda output: plot_emergence_timeline(tables["emergence"], output, top_n=int(settings.get("top_n_labels", 30))),
        "feature_prevalence": lambda output: plot_feature_prevalence(tables["species_feature_summary"], output, top_n=int(settings.get("top_n_features", 30))),
        "annual_evidence_counts": lambda output: plot_annual_evidence_counts(tables["evidence_records"], output),
        "taxonomy_composition": lambda output: plot_taxonomy_composition(tables["species_summary"], output, rank=str(settings.get("taxonomy_plot_rank", "phylum")), top_n=int(settings.get("top_n_taxa", 20))),
        "species_feature_space": lambda output: plot_species_feature_space(tables["species_feature_matrix"], output, max_labels=int(settings.get("top_n_labels", 30))),
    }
    figures = {}
    for index, specification in enumerate(specifications, start=1):
        figure_id = str(specification.get("id", ""))
        if figure_id not in plotters:
            raise ValueError(f"Unknown Phase 5 plot id {figure_id!r} in {plot_specs_path}.")
        output_path = plot_dir / str(specification.get("output", f"{figure_id}.png"))
        print(f"Phase 5: generating figure {index}/{len(specifications)}: {figure_id}", flush=True)
        figures[figure_id] = str(plotters[figure_id](output_path))
    metadata = {"status": "completed", "source": "pathogen_atlas_v2_phase5", "figure_count": len(figures), "outputs": figures, "inputs": {name: details["path"] for name, details in preflight["inputs"].items()}}
    metadata_path = ensure_parent(plot_dir / "plot_creation_metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Phase 5: generated {len(figures)} figures in {plot_dir}", flush=True)
    return metadata


def main() -> dict[str, Any]:
    """Run Phase 5 only when enabled in notebook parameters."""
    config = notebook_phase_parameters("phase5_plot_creation")
    if not bool(config.get("run_phase", False)):
        result = {**phase5_preflight(config), "status": "preflight_only", "reason": "Set run_phase: true to generate the Phase 5 plots."}
    else:
        result = build_phase5_plots(config)
    print(result)
    return result


if __name__ == "__main__":
    main()
