"""Utilities for thin orchestration notebooks."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pandas as pd

from pathogen_atlas_v2.common.io import load_yaml, resolve_path
from pathogen_atlas_v2.species.acquire import SpeciesAcquisitionError
from pathogen_atlas_v2.species.export_mappings import run_phase1


def phase_status(phase: int, name: str, implemented: bool) -> pd.DataFrame:
    """Return a small status table for a notebook phase."""
    return pd.DataFrame(
        [
            {
                "phase": phase,
                "name": name,
                "implemented": implemented,
            }
        ]
    )


def run_phase_1(config_path: str = "configs/species_sources.yaml", force_refresh: bool = False) -> dict[str, Any]:
    """Run Phase 1 and convert expected setup errors into notebook-friendly output."""
    try:
        return run_phase1(config_path=config_path, force_refresh=force_refresh)
    except SpeciesAcquisitionError as exc:
        return {"status": "not_run", "reason": str(exc)}


def run_pipeline_module(module_name: str) -> dict[str, Any]:
    """Run a pipeline module's ``main`` function."""
    module = importlib.import_module(module_name)
    try:
        module.main()
    except NotImplementedError as exc:
        return {"status": "not_implemented", "reason": str(exc)}
    return {"status": "completed", "module": module_name}


def phase1_output_paths(config_path: str = "configs/species_sources.yaml") -> dict[str, Path]:
    """Return configured Phase 1 output paths."""
    config = load_yaml(resolve_path(config_path))
    source = next(item for item in config["sources"] if item["name"] == "lpsn")
    release = source["release"]
    return {
        "normalised_records": resolve_path(source["normalised_records_path"]),
        "species_canonical": resolve_path(release["species_canonical_path"]),
        "species_synonym_mapping": resolve_path(release["species_synonym_mapping_path"]),
        "species_taxonomy": resolve_path(release["species_taxonomy_path"]),
        "metadata": resolve_path(release["metadata_path"]),
    }


def existing_outputs(paths: dict[str, Path]) -> pd.DataFrame:
    """Summarise configured output files."""
    rows = []
    for name, path in paths.items():
        rows.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
            }
        )
    return pd.DataFrame(rows)


def read_table_if_exists(path: str | Path, n_rows: int = 10) -> pd.DataFrame:
    """Read a parquet or CSV table if it exists, otherwise return an empty status table."""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame([{"status": "missing", "path": str(path)}])
    if path.suffix == ".parquet":
        return pd.read_parquet(path).head(n_rows)
    if path.suffix == ".csv":
        return pd.read_csv(path, nrows=n_rows)
    return pd.DataFrame([{"status": "unsupported", "path": str(path)}])


def phase_not_implemented(phase: int, name: str, pipeline_module: str) -> pd.DataFrame:
    """Return a placeholder table for phases whose pipeline modules are stubs."""
    return pd.DataFrame(
        [
            {
                "phase": phase,
                "name": name,
                "pipeline_module": pipeline_module,
                "status": "not_implemented",
            }
        ]
    )
