"""Utilities for thin orchestration notebooks."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pandas as pd

from pathogen_atlas_v2.abstracts.abstract_exports import run_phase2
from pathogen_atlas_v2.abstracts.pubmed_client import PubMedClientError
from pathogen_atlas_v2.abstracts.pubmed_query_plan import (
    PubMedQueryPlanError,
    materialize_pubmed_query_plan,
)
from pathogen_atlas_v2.abstracts.pubmed_query_templates import load_pubmed_query_template_config
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


def materialize_phase_2_query_plan(
    template_config_path: str = "configs/pubmed_query_templates.yaml",
    filter_config_path: str = "configs/abstract_filters.yaml",
) -> dict[str, Any]:
    """Generate the inspectable Phase 2 PubMed query plan."""
    try:
        return materialize_pubmed_query_plan(template_config_path, filter_config_path)
    except PubMedQueryPlanError as exc:
        return {"status": "not_run", "reason": str(exc)}


def run_phase_2(
    template_config_path: str = "configs/pubmed_query_templates.yaml",
    filter_config_path: str = "configs/abstract_filters.yaml",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Run Phase 2 and convert expected setup errors into notebook-friendly output."""
    try:
        return run_phase2(template_config_path, filter_config_path, force_refresh=force_refresh)
    except (PubMedClientError, PubMedQueryPlanError) as exc:
        return {"status": "not_run", "reason": str(exc)}


def metadata_overview(metadata: dict[str, Any]) -> pd.DataFrame:
    """Return compact top-level metadata fields as a DataFrame."""
    if not metadata:
        return pd.DataFrame([{"status": "missing"}])
    rows = []
    for key in ("status", "source", "source_retrieved_at", "generated_at", "reason"):
        if key in metadata:
            rows.append({"field": key, "value": metadata[key]})
    if "query_plan" in metadata:
        query_plan = metadata["query_plan"]
        rows.append({"field": "query_plan_path", "value": query_plan.get("path")})
        rows.append({"field": "query_plan_approval", "value": query_plan.get("approval", {}).get("status")})
        rows.append({"field": "query_count", "value": query_plan.get("query_count")})
    return pd.DataFrame(rows) if rows else pd.DataFrame([{"status": "no_summary_fields"}])


def metadata_counts(metadata: dict[str, Any]) -> pd.DataFrame:
    """Return nested count dictionaries as a tidy DataFrame."""
    rows = []
    for section in ("record_counts", "mapping_diagnostics"):
        values = metadata.get(section, {}) if isinstance(metadata, dict) else {}
        for metric, value in values.items():
            rows.append({"section": section, "metric": metric, "value": value})
    return pd.DataFrame(rows) if rows else pd.DataFrame([{"status": "no_counts"}])


def metadata_package_versions(metadata: dict[str, Any]) -> pd.DataFrame:
    """Return package versions as a DataFrame."""
    versions = metadata.get("package_versions", {}) if isinstance(metadata, dict) else {}
    rows = [{"package": package, "version": version} for package, version in versions.items()]
    return pd.DataFrame(rows) if rows else pd.DataFrame([{"status": "no_package_versions"}])


def metadata_outputs(metadata: dict[str, Any]) -> pd.DataFrame:
    """Return output paths as a DataFrame."""
    outputs = metadata.get("outputs", {}) if isinstance(metadata, dict) else {}
    rows = []
    for name, path in outputs.items():
        output_path = Path(path)
        rows.append(
            {
                "name": name,
                "path": str(output_path),
                "exists": output_path.exists(),
                "size_bytes": output_path.stat().st_size if output_path.exists() else 0,
            }
        )
    return pd.DataFrame(rows) if rows else pd.DataFrame([{"status": "no_outputs"}])


def metadata_v1_comparison(metadata: dict[str, Any]) -> pd.DataFrame:
    """Return Phase 1 V1 diagnostic comparison as a compact DataFrame."""
    comparison = metadata.get("v1_comparison", {}) if isinstance(metadata, dict) else {}
    rows = []
    for key, value in comparison.items():
        if key == "files":
            continue
        rows.append({"metric": key, "value": value})
    return pd.DataFrame(rows) if rows else pd.DataFrame([{"status": "no_v1_comparison"}])


def phase_run_summary(metadata: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Return standard notebook display tables for pipeline metadata."""
    return {
        "overview": metadata_overview(metadata),
        "counts": metadata_counts(metadata),
        "package_versions": metadata_package_versions(metadata),
        "outputs": metadata_outputs(metadata),
    }


def query_plan_result_summary(result: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Return display tables for query-plan materialization results."""
    overview = metadata_overview(
        {
            "status": result.get("status", "generated" if result.get("path") else "unknown"),
            "query_plan": {
                "path": result.get("path"),
                "approval": result.get("approval", {}),
                "query_count": result.get("query_count"),
            },
            "reason": result.get("reason"),
        }
    )
    return {"overview": overview}


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


def phase2_output_paths(config_path: str = "configs/pubmed_query_templates.yaml") -> dict[str, Path]:
    """Return configured Phase 2 query-plan and output paths."""
    config = load_pubmed_query_template_config(config_path)
    paths = {"query_plan": resolve_path(config["query_plan"]["path"])}
    paths.update({key: resolve_path(path) for key, path in config["outputs"].items()})
    return paths


def phase2_query_plan_preview(query_plan_path: str | Path, n_rows: int = 20) -> pd.DataFrame:
    """Return a compact preview of rendered Phase 2 PubMed queries."""
    path = Path(query_plan_path)
    if not path.exists():
        return pd.DataFrame([{"status": "missing", "path": str(path)}])
    plan = load_yaml(path)
    rows = []
    for query in plan.get("queries", [])[:n_rows]:
        rows.append(
            {
                "query_id": query.get("query_id"),
                "canonical_species": query.get("canonical_species"),
                "matched_name": query.get("matched_name"),
                "field": query.get("field"),
                "template_id": query.get("template_id"),
                "query": query.get("query"),
            }
        )
    return pd.DataFrame(rows)


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
