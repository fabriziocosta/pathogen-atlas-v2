"""Utilities for thin orchestration notebooks."""

from __future__ import annotations

import importlib
import json
import os
import textwrap
from numbers import Number
from pathlib import Path
from typing import Any

import pandas as pd

from pathogen_atlas_v2.abstracts.abstract_exports import (
    _read_jsonl_resume_records,
    run_phase2,
    run_phase2_abstract_fetch,
    run_phase2_pmid_acquisition,
)
from pathogen_atlas_v2.abstracts.pubmed_client import PubMedClientError, load_ncbi_entrez_config
from pathogen_atlas_v2.abstracts.pubmed_query_plan import (
    PubMedQueryPlanError,
    render_query_records,
    materialize_pubmed_query_plan,
)
from pathogen_atlas_v2.abstracts.pubmed_query_templates import (
    load_abstract_filter_terms,
    load_pubmed_query_template_config,
    render_pubmed_query,
)
from pathogen_atlas_v2.common.io import ensure_parent, load_yaml, resolve_path
from pathogen_atlas_v2.species.acquire import SpeciesAcquisitionError
from pathogen_atlas_v2.species.acquire import load_progress as load_phase1_progress_json
from pathogen_atlas_v2.species.export_mappings import run_phase1


NOTEBOOK_PARAMETERS_PATH = "configs/notebook_parameters.yaml"
DEFAULT_NOTEBOOK_PARAMETERS: dict[str, Any] = {
    "phase1_species_synonyms": {
        "config_path": "configs/species_sources.yaml",
        "force_refresh": False,
        "resume": True,
        "run_phase": False,
        "enrich_taxonomy_hierarchy": True,
    },
    "phase2_query_plan": {
        "template_config_path": "configs/pubmed_query_templates.yaml",
        "filter_config_path": "configs/abstract_filters.yaml",
        "preview_rows": 20,
    },
    "phase2_pmid_acquisition": {
        "template_config_path": "configs/pubmed_query_templates.yaml",
        "filter_config_path": "configs/abstract_filters.yaml",
        "force_refresh": False,
        "run_pubmed_search": False,
        "log_every": 100,
    },
    "phase2_abstract_fetch": {
        "template_config_path": "configs/pubmed_query_templates.yaml",
        "filter_config_path": "configs/abstract_filters.yaml",
        "force_refresh": False,
        "run_abstract_fetch": False,
        "log_every": 10,
    },
    "phase2_table_stats": {
        "template_config_path": "configs/pubmed_query_templates.yaml",
        "preview_rows": 10,
    },
    "phase3_abstract_decisions": {
        "run_phase": False,
        "pipeline_module": "pathogen_atlas_v2.pipelines.phase3_abstract_decisions",
        "provider": "openrouter_jev",
        "model": "typesafe/jev-1.14",
        "question_key": "human_pathogen_evidence",
        "question_config_path": "configs/llm_questions.yaml",
        "pubmed_config_path": "configs/pubmed_query_templates.yaml",
        "run_id": "jev-human-pathogen-evidence-v1",
        "max_calls": 25,
        "confidence_threshold": 0.75,
        "expert_question_config_path": "configs/domain_expert_questions.yaml",
        "expert_run_id": "jev-domain-expert-features-v1",
        "expert_max_calls": 25,
        "expert_checkpoint_every": 25,
        "question_confidence_threshold": 0.75,
        "max_workers": 4,
        "retries": 3,
        "backoff_seconds": 1.0,
        "timeout_seconds": 90.0,
        "checkpoint_every": 25,
        "decision_path": "data/03_decisions/parsed/pathogen_evidence_jev.parquet",
        "accepted_decisions_path": "data/03_decisions/parsed/pathogen_evidence_jev_accepted.parquet",
        "expert_decision_path": "data/03_decisions/parsed/domain_expert_probabilities.parquet",
        "expert_answers_path": "data/03_decisions/parsed/domain_expert_answers.parquet",
        "expert_raw_response_path": "data/03_decisions/raw_responses/jev/domain_expert_questions.jsonl",
        "raw_response_path": "data/03_decisions/raw_responses/jev/pathogen_evidence.jsonl",
    },
    "phase4_table_creation": {
        "run_phase": False,
        "pipeline_module": "pathogen_atlas_v2.pipelines.04_build_tables",
    },
    "phase5_plot_creation": {
        "run_phase": False,
        "pipeline_module": "pathogen_atlas_v2.pipelines.05_make_plots",
    },
}


def _deep_merge_dicts(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Return a recursive merge without mutating inputs."""
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_notebook_parameters(path: str | Path = NOTEBOOK_PARAMETERS_PATH) -> dict[str, Any]:
    """Load notebook parameters, falling back to tracked defaults."""
    config_path = resolve_path(path)
    if not config_path.exists():
        return dict(DEFAULT_NOTEBOOK_PARAMETERS)
    loaded = load_yaml(config_path)
    if not isinstance(loaded, dict):
        loaded = {}
    return _deep_merge_dicts(DEFAULT_NOTEBOOK_PARAMETERS, loaded)


def save_notebook_parameters(parameters: dict[str, Any], path: str | Path = NOTEBOOK_PARAMETERS_PATH) -> dict[str, Any]:
    """Save notebook parameters as YAML and return a small result dictionary."""
    import yaml

    merged = _deep_merge_dicts(DEFAULT_NOTEBOOK_PARAMETERS, parameters)
    config_path = ensure_parent(resolve_path(path))
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(merged, handle, sort_keys=False, allow_unicode=False)
    return {"status": "saved", "path": str(config_path), "phase_count": len(merged)}


def notebook_phase_parameters(phase: str, path: str | Path = NOTEBOOK_PARAMETERS_PATH) -> dict[str, Any]:
    """Return parameters for one notebook phase."""
    parameters = load_notebook_parameters(path)
    return dict(parameters.get(phase, {}))


def notebook_parameters_overview(parameters: dict[str, Any] | None = None) -> pd.DataFrame:
    """Return a compact table of configured notebook phase parameters."""
    parameters = parameters or load_notebook_parameters()
    rows = []
    for phase, values in parameters.items():
        row = {"phase": phase}
        if isinstance(values, dict):
            row.update(values)
        rows.append(row)
    return pd.DataFrame(rows)


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


def run_phase_1(
    config_path: str = "configs/species_sources.yaml",
    force_refresh: bool = False,
    resume: bool = True,
    enrich_taxonomy_hierarchy: bool | None = None,
) -> dict[str, Any]:
    """Run Phase 1 and convert expected setup errors into notebook-friendly output."""
    try:
        return run_phase1(
            config_path=config_path,
            force_refresh=force_refresh,
            resume=resume,
            log=True,
            enrich_taxonomy_hierarchy=enrich_taxonomy_hierarchy,
        )
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


def run_phase_2_pmid_acquisition(
    template_config_path: str = "configs/pubmed_query_templates.yaml",
    filter_config_path: str = "configs/abstract_filters.yaml",
    force_refresh: bool = False,
    log_every: int = 25,
) -> dict[str, Any]:
    """Run resumable Phase 2 PMID acquisition."""
    try:
        return run_phase2_pmid_acquisition(
            template_config_path,
            filter_config_path,
            force_refresh=force_refresh,
            log=True,
            log_every=log_every,
        )
    except (PubMedClientError, PubMedQueryPlanError) as exc:
        return {"status": "not_run", "reason": str(exc)}


def run_phase_2_abstract_fetch(
    template_config_path: str = "configs/pubmed_query_templates.yaml",
    filter_config_path: str = "configs/abstract_filters.yaml",
    force_refresh: bool = False,
    log_every: int = 10,
) -> dict[str, Any]:
    """Run resumable Phase 2 abstract fetching and table export."""
    try:
        return run_phase2_abstract_fetch(
            template_config_path,
            filter_config_path,
            force_refresh=force_refresh,
            log=True,
            log_every=log_every,
        )
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
    if not rows and isinstance(metadata, dict) and metadata.get("status") == "not_run":
        return pd.DataFrame(
            [
                {
                    "section": "run_status",
                    "metric": "not_run_reason",
                    "value": metadata.get("reason", "not_run"),
                }
            ]
        )
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
        row = output_path_summary(path)
        row["name"] = name
        rows.append(row)
    return pd.DataFrame(rows)[["name", "path", "exists", "rows", "size_bytes", "content_type"]] if rows else pd.DataFrame([{"status": "no_outputs"}])


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
        result = module.main()
    except NotImplementedError as exc:
        return {"status": "not_implemented", "reason": str(exc)}
    if isinstance(result, dict):
        return result
    return {"status": "completed", "module": module_name}


def phase1_output_paths(config_path: str = "configs/species_sources.yaml") -> dict[str, Path]:
    """Return configured Phase 1 output paths."""
    config = load_yaml(resolve_path(config_path))
    source = next(item for item in config["sources"] if item["name"] == "lpsn")
    release = source["release"]
    return {
        "candidate_records": resolve_path(source.get("candidate_records_path", "data/01_species/raw/lpsn_species_candidates.jsonl")),
        "raw_records": resolve_path(source.get("raw_records_path", "data/01_species/raw/lpsn_species_records.jsonl")),
        "lookup_cache": resolve_path(source.get("lookup_cache_path", "data/01_species/raw/lpsn_lookup_cache.jsonl")),
        "progress": resolve_path(source.get("progress_path", "data/01_species/raw/lpsn_species_progress.json")),
        "normalised_records": resolve_path(source["normalised_records_path"]),
        "species_canonical": resolve_path(release["species_canonical_path"]),
        "species_synonym_mapping": resolve_path(release["species_synonym_mapping_path"]),
        "species_mapping_conflicts": resolve_path(
            release.get(
                "species_mapping_conflicts_path",
                str(resolve_path(release["species_synonym_mapping_path"]).with_name("species_mapping_conflicts.parquet")),
            )
        ),
        "species_taxonomy": resolve_path(release["species_taxonomy_path"]),
        "metadata": resolve_path(release["metadata_path"]),
    }


def phase1_progress_status(config_path: str = "configs/species_sources.yaml") -> pd.DataFrame:
    """Return current Phase 1 live acquisition progress as a DataFrame."""
    config = load_yaml(resolve_path(config_path))
    source = next(item for item in config["sources"] if item["name"] == "lpsn")
    progress_path = resolve_path(source.get("progress_path", "data/01_species/raw/lpsn_species_progress.json"))
    progress = load_phase1_progress_json(progress_path)
    total = progress.get("total_records")
    completed = progress.get("completed_records")
    retrieved = progress.get("retrieved_records") or progress.get("candidate_records")
    remaining = progress.get("remaining_records")
    percent = None
    if isinstance(total, Number) and total:
        percent = round((float(completed or 0) / float(total)) * 100, 2)
    return pd.DataFrame(
        [
            {
                "status": progress.get("status", "not_started"),
                "mode": progress.get("mode"),
                "retrieved_records": retrieved,
                "completed_records": completed,
                "total_records": total,
                "remaining_records": remaining,
                "percent_complete": percent,
                "enrich_taxonomy_hierarchy": progress.get("enrich_taxonomy_hierarchy", source.get("enrich_taxonomy_hierarchy", False)),
                "updated_at": progress.get("updated_at"),
                "path": str(progress_path),
            }
        ]
    )


def phase2_output_paths(config_path: str = "configs/pubmed_query_templates.yaml") -> dict[str, Path]:
    """Return configured Phase 2 query-plan and output paths."""
    config = load_pubmed_query_template_config(config_path)
    paths = {"query_plan": resolve_path(config["query_plan"]["path"])}
    paths.update({key: resolve_path(path) for key, path in config["outputs"].items()})
    return paths


def _format_seconds(seconds: float) -> str:
    seconds = max(int(round(seconds)), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def phase2_pmid_acquisition_preflight(
    template_config_path: str = "configs/pubmed_query_templates.yaml",
    filter_config_path: str = "configs/abstract_filters.yaml",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Return a local preflight summary for resumable PMID acquisition."""
    result = materialize_phase_2_query_plan(template_config_path, filter_config_path)
    config = load_pubmed_query_template_config(template_config_path)
    paths = phase2_output_paths(template_config_path)
    source = config["source"]
    query_count = int(result.get("query_count") or 0)
    existing_results = []
    if paths["raw_search_results_path"].exists() and not force_refresh:
        existing_results = _read_jsonl_resume_records(paths["raw_search_results_path"], log=False)
    completed = len({record.get("query_id") for record in existing_results if record.get("query_id")})
    pending = query_count if force_refresh else max(query_count - completed, 0)
    ncbi_config_path = resolve_path(source["ncbi_config_path"])
    ncbi_config = load_yaml(ncbi_config_path) if ncbi_config_path.exists() else {}
    ncbi = ncbi_config.get("ncbi", {}) if isinstance(ncbi_config, dict) else {}
    api_key_configured = bool(
        ncbi.get("api_key") or os.getenv("NCBI_API_KEY") or os.getenv("EUTILS_API_KEY")
    )
    if ncbi_config_path.exists():
        try:
            api_key_configured = bool(load_ncbi_entrez_config(ncbi_config_path).api_key)
        except PubMedClientError:
            # Keep preflight useful for incomplete local config files; actual runs
            # will report the configuration error when they construct the client.
            pass
    configured_delay = source.get("request_delay_seconds")
    if configured_delay is None:
        rate_key = "rate_limit_with_api_key_per_second" if api_key_configured else "rate_limit_without_api_key_per_second"
        requests_per_second = float(source.get(rate_key, 0) or 0)
        request_delay = 1 / requests_per_second if requests_per_second > 0 else 0.0
    else:
        request_delay = float(configured_delay)
    lower_bound_seconds = pending * request_delay
    return pd.DataFrame(
        [
            {
                "query_count": query_count,
                "completed_queries": 0 if force_refresh else completed,
                "pending_queries": pending,
                "force_refresh": force_refresh,
                "fields": ", ".join(source.get("fields", [])),
                "request_delay_seconds": request_delay,
                "lower_bound_runtime": _format_seconds(lower_bound_seconds),
                "raw_search_results_path": str(paths["raw_search_results_path"]),
                "ncbi_config_exists": ncbi_config_path.exists(),
                "ncbi_email": ncbi.get("email", ""),
                "ncbi_api_key_configured": api_key_configured,
            }
        ]
    )


def phase2_query_plan_preview(query_plan_path: str | Path, n_rows: int = 20) -> pd.DataFrame:
    """Return a compact preview of rendered Phase 2 PubMed queries."""
    path = Path(query_plan_path)
    if not path.exists():
        return pd.DataFrame([{"status": "missing", "path": str(path)}])
    plan = load_yaml(path)
    queries = plan.get("queries")
    if queries is None and "synonym_groups" in plan:
        preview_plan = {**plan, "synonym_groups": plan.get("synonym_groups", [])[:n_rows]}
        queries = render_query_records(preview_plan)[:n_rows]
    else:
        queries = (queries or [])[:n_rows]
    rows = []
    for query in queries:
        rows.append(
            {
                "query_id": query.get("query_id"),
                "canonical_species": query.get("canonical_species"),
                "synonym_count": query.get("synonym_count"),
                "matched_names": ", ".join(query.get("matched_names", [])[:6]),
                "field": query.get("field"),
                "template_id": query.get("template_id"),
                "query": query.get("query"),
            }
        )
    return pd.DataFrame(rows)


def phase2_synonym_counts(query_plan_path: str | Path) -> pd.DataFrame:
    """Return one row per canonical species with matched-name and synonym counts."""
    path = Path(query_plan_path)
    if not path.exists():
        return pd.DataFrame([{"status": "missing", "path": str(path)}])
    plan = load_yaml(path)
    rows = []
    by_species: dict[str, dict[str, Any]] = {}
    items = plan.get("synonym_groups") or plan.get("queries", [])
    for item in items:
        canonical_species = item.get("canonical_species")
        if not canonical_species:
            continue
        matched_names = list(dict.fromkeys(item.get("matched_names", [])))
        current = by_species.get(canonical_species)
        if current is None or len(matched_names) > len(current["matched_names"]):
            by_species[canonical_species] = {
                "canonical_species": canonical_species,
                "matched_names": matched_names,
            }
    for item in by_species.values():
        rows.append(
            {
                "canonical_species": item["canonical_species"],
                "matched_name_count": len(item["matched_names"]),
                "synonym_count": max(len(item["matched_names"]) - 1, 0),
                "matched_names": ", ".join(item["matched_names"]),
            }
        )
    if not rows:
        return pd.DataFrame([{"status": "empty_query_plan", "path": str(path)}])
    return pd.DataFrame(rows).sort_values(["synonym_count", "canonical_species"], ascending=[False, True]).reset_index(drop=True)


def phase2_synonym_distribution_summary(query_plan_path: str | Path) -> pd.DataFrame:
    """Return summary statistics for the number of synonyms per canonical species."""
    counts = phase2_synonym_counts(query_plan_path)
    if "synonym_count" not in counts.columns:
        return counts
    synonyms = counts["synonym_count"]
    return pd.DataFrame(
        [
            {
                "canonical_species": int(len(counts)),
                "species_with_synonyms": int((synonyms > 0).sum()),
                "species_without_synonyms": int((synonyms == 0).sum()),
                "mean_synonyms": float(synonyms.mean()),
                "median_synonyms": float(synonyms.median()),
                "max_synonyms": int(synonyms.max()),
            }
        ]
    )


def phase2_synonym_distribution_histogram(query_plan_path: str | Path, bar_width: int = 60) -> pd.DataFrame:
    """Return a compact histogram table for synonym counts per canonical species."""
    counts = phase2_synonym_counts(query_plan_path)
    if "synonym_count" not in counts.columns:
        return counts
    histogram = counts["synonym_count"].value_counts().sort_index().rename_axis("synonym_count").reset_index(name="species_count")
    max_count = int(histogram["species_count"].max()) if not histogram.empty else 0
    histogram["fraction"] = histogram["species_count"] / int(histogram["species_count"].sum())
    histogram["histogram"] = histogram["species_count"].apply(
        lambda value: "#" * max(1, round((int(value) / max_count) * bar_width)) if max_count else ""
    )
    return histogram


def plot_phase2_synonym_distribution(query_plan_path: str | Path):
    """Render a matplotlib histogram of synonym counts per canonical species."""
    import matplotlib.pyplot as plt

    counts = phase2_synonym_counts(query_plan_path)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if "synonym_count" not in counts.columns:
        ax.text(0.5, 0.5, "No synonym counts available", ha="center", va="center")
        ax.set_axis_off()
        return fig

    values = counts["synonym_count"].astype(int)
    max_value = int(values.max()) if not values.empty else 0
    bins = range(0, max_value + 2)
    bar_values = values.value_counts().sort_index()
    bars = ax.bar(
        bar_values.index.astype(int),
        bar_values.values.astype(int),
        width=0.82,
        align="center",
        color="#2f6f9f",
        edgecolor="#1d3f5e",
    )
    ax.set_yscale("log")
    for bar in bars:
        count = int(bar.get_height())
        ax.annotate(
            f"{count}",
            xy=(bar.get_x() + bar.get_width() / 2, count),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    ax.set_title("Synonyms Per Canonical Species")
    ax.set_xlabel("Number of synonyms")
    ax.set_ylabel("Canonical species")
    ax.set_xticks(list(range(0, max_value + 1)))
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    return fig


def phase2_query_template_text(config_path: str = "configs/pubmed_query_templates.yaml", template_id: str | None = None) -> str:
    """Return a tracked PubMed query template string with placeholders."""
    config = load_pubmed_query_template_config(config_path)
    templates = config.get("templates", [])
    if not templates:
        return "No query templates configured."
    if template_id is None:
        return str(templates[0].get("template", ""))
    for template in templates:
        if template.get("id") == template_id:
            return str(template.get("template", ""))
    return f"Template not found: {template_id}"


FALLBACK_SYNONYM_EXAMPLE_NAMES = ["Abiotrophia adiacens", "Granulicatella adiacens"]
FALLBACK_SYNONYM_EXAMPLE_CANONICAL = "Granulicatella adiacens"


def _phase2_select_query_example(
    query_plan_path: str | Path,
    template_config_path: str = "configs/pubmed_query_templates.yaml",
    filter_config_path: str = "configs/abstract_filters.yaml",
    query_index: int = 0,
    prefer_synonyms: bool = True,
) -> dict[str, Any]:
    """Select a concrete query example, preferring the richest synonym query."""
    path = Path(query_plan_path)
    if not path.exists():
        return {"status": "missing", "path": str(path), "query": f"Query plan missing: {path}"}
    plan = load_yaml(path)
    queries = plan.get("queries", [])
    if not queries and "synonym_groups" in plan:
        groups = plan.get("synonym_groups", [])
        if prefer_synonyms:
            groups = sorted(groups, key=lambda item: len(item.get("matched_names", [])), reverse=True)
        else:
            query_index = min(max(query_index, 0), len(groups) - 1) if groups else 0
            groups = groups[query_index : query_index + 1]
        if groups:
            preview_plan = {**plan, "synonym_groups": groups[:1]}
            query = render_query_records(preview_plan)[0]
            return {"status": "from_compact_query_plan", **query}
    if not queries:
        return {"status": "empty_query_plan", "path": str(path), "query": "Query plan contains no rendered queries."}
    if prefer_synonyms:
        query = max(queries, key=lambda item: len(item.get("matched_names", [])))
        if len(query.get("matched_names", [])) > 1:
            return {"status": "from_query_plan", **query}
        config = load_pubmed_query_template_config(template_config_path)
        terms = load_abstract_filter_terms(filter_config_path)
        template = config["templates"][0]
        source = config["source"]
        field = str(source["fields"][0])
        return {
            "status": "fallback_no_synonyms_in_query_plan",
            "canonical_species": FALLBACK_SYNONYM_EXAMPLE_CANONICAL,
            "matched_names": FALLBACK_SYNONYM_EXAMPLE_NAMES,
            "mapping_types": ["accepted", "synonym"],
            "synonym_count": len(FALLBACK_SYNONYM_EXAMPLE_NAMES),
            "field": field,
            "template_id": template["id"],
            "query": render_pubmed_query(
                template=template["template"],
                matched_names=FALLBACK_SYNONYM_EXAMPLE_NAMES,
                field=field,
                human_terms=terms["human_context"],
                infection_terms=terms["infection_context"],
                min_year=int(source["min_year"]),
                max_year=int(source["max_year"]),
            ),
        }
    query_index = min(max(query_index, 0), len(queries) - 1)
    return {"status": "from_query_plan", **queries[query_index]}


def phase2_concrete_query_example_text(
    query_plan_path: str | Path,
    template_config_path: str = "configs/pubmed_query_templates.yaml",
    filter_config_path: str = "configs/abstract_filters.yaml",
    query_index: int = 0,
    prefer_synonyms: bool = True,
) -> str:
    """Return one fully rendered PubMed query string."""
    query = _phase2_select_query_example(
        query_plan_path,
        template_config_path=template_config_path,
        filter_config_path=filter_config_path,
        query_index=query_index,
        prefer_synonyms=prefer_synonyms,
    )
    return str(query.get("query", ""))


def phase2_concrete_query_example_metadata(
    query_plan_path: str | Path,
    template_config_path: str = "configs/pubmed_query_templates.yaml",
    filter_config_path: str = "configs/abstract_filters.yaml",
    query_index: int = 0,
    prefer_synonyms: bool = True,
) -> pd.DataFrame:
    """Return metadata for the selected concrete PubMed query example."""
    query = _phase2_select_query_example(
        query_plan_path,
        template_config_path=template_config_path,
        filter_config_path=filter_config_path,
        query_index=query_index,
        prefer_synonyms=prefer_synonyms,
    )
    return pd.DataFrame(
        [
            {
                "status": query.get("status"),
                "canonical_species": query.get("canonical_species"),
                "synonym_count": query.get("synonym_count"),
                "field": query.get("field"),
                "template_id": query.get("template_id"),
                "matched_names": ", ".join(query.get("matched_names", [])),
            }
        ]
    )


def wrap_text(text: str, width: int = 100) -> str:
    """Wrap long display text for readable notebook output."""
    return textwrap.fill(str(text), width=width, break_long_words=False, break_on_hyphens=False)


def existing_outputs(paths: dict[str, Path]) -> pd.DataFrame:
    """Summarise configured output files."""
    rows = []
    for name, path in paths.items():
        row = output_path_summary(path)
        row["name"] = name
        rows.append(row)
    return pd.DataFrame(rows)[["name", "path", "exists", "rows", "size_bytes", "content_type"]]


def output_path_summary(path: str | Path) -> dict[str, Any]:
    """Summarise an output file with row/entry counts when possible."""
    path = Path(path)
    row: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "rows": None,
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "content_type": path.suffix.lstrip(".") or "unknown",
    }
    if not path.exists() or path.is_dir():
        return row
    try:
        if path.suffix == ".parquet":
            row["rows"] = int(pd.read_parquet(path).shape[0])
        elif path.suffix == ".csv":
            row["rows"] = int(sum(1 for _ in path.open("r", encoding="utf-8")) - 1)
        elif path.suffix == ".jsonl":
            row["rows"] = int(sum(1 for line in path.open("r", encoding="utf-8") if line.strip()))
        elif path.suffix == ".json":
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            row["rows"] = len(data) if isinstance(data, list) else 1
    except Exception as exc:
        row["rows"] = None
        row["content_type"] = f"{row['content_type']}: count_error={type(exc).__name__}"
    return row


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


def table_stats_if_exists(path: str | Path) -> pd.DataFrame:
    """Return compact stats for a parquet or CSV table."""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame([{"path": str(path), "exists": False}])
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix == ".csv":
        df = pd.read_csv(path)
    else:
        return pd.DataFrame([{"path": str(path), "exists": True, "status": "unsupported"}])
    return pd.DataFrame(
        [
            {
                "path": str(path),
                "exists": True,
                "rows": len(df),
                "columns": len(df.columns),
                "size_bytes": path.stat().st_size,
            }
        ]
    )


def phase2_table_stats(config_path: str = "configs/pubmed_query_templates.yaml") -> pd.DataFrame:
    """Return stats for the Phase 2 parquet tables."""
    paths = phase2_output_paths(config_path)
    table_keys = [
        "canonical_pmid_index_path",
        "sampled_canonical_pmid_index_path",
        "filtered_species_matches_path",
        "filtered_filter_matches_path",
        "release_abstracts_path",
        "release_species_matches_path",
        "release_filter_matches_path",
    ]
    frames = []
    for key in table_keys:
        stats = table_stats_if_exists(paths[key])
        stats.insert(0, "table", key)
        frames.append(stats)
    return pd.concat(frames, ignore_index=True)


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
