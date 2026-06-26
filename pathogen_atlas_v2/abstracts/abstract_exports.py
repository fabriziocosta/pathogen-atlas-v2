"""Run Phase 2 PubMed acquisition and export release tables."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from pathogen_atlas_v2.abstracts.article_records import build_articles_dataframe
from pathogen_atlas_v2.abstracts.filter_matches import build_filter_match_rows
from pathogen_atlas_v2.abstracts.pubmed_client import PubMedEntrezClient, load_ncbi_entrez_config
from pathogen_atlas_v2.abstracts.pubmed_query_plan import (
    load_pubmed_query_plan,
    materialize_pubmed_query_plan,
)
from pathogen_atlas_v2.abstracts.pubmed_query_templates import load_pubmed_query_template_config
from pathogen_atlas_v2.abstracts.species_matches import build_species_match_rows
from pathogen_atlas_v2.common.io import ensure_parent, read_jsonl, resolve_path, write_json, write_jsonl


def _output_paths(config: dict[str, Any]) -> dict[str, Path]:
    return {key: resolve_path(path) for key, path in config["outputs"].items()}


def _query_plan_path(config: dict[str, Any]) -> Path:
    return resolve_path(config["query_plan"]["path"])


def run_pubmed_searches(query_plan: dict[str, Any], client: PubMedEntrezClient) -> list[dict[str, Any]]:
    """Run PubMed searches for all rendered queries."""
    results = []
    for query in query_plan.get("queries", []):
        pmids = client.search_pmids(query["query"])
        results.append({"query_id": query["query_id"], "query_hash": query["query_hash"], "pmids": pmids})
    return results


def run_pubmed_fetches(search_results: list[dict[str, Any]], client: PubMedEntrezClient) -> list[dict[str, Any]]:
    """Fetch article records for unique PMIDs in search results."""
    pmids = sorted({str(pmid) for result in search_results for pmid in result.get("pmids", [])})
    return client.fetch_medline_records(pmids)


def write_phase2_outputs(
    query_plan: dict[str, Any],
    search_results: list[dict[str, Any]],
    article_records: list[dict[str, Any]],
    template_config_path: str | Path = "configs/pubmed_query_templates.yaml",
    filter_config_path: str | Path = "configs/abstract_filters.yaml",
) -> dict[str, Any]:
    """Write Phase 2 raw, filtered, release, and metadata outputs."""
    config = load_pubmed_query_template_config(template_config_path)
    paths = _output_paths(config)

    write_jsonl(paths["raw_search_results_path"], search_results)
    write_jsonl(paths["raw_article_records_path"], article_records)

    articles_df = build_articles_dataframe(article_records)
    species_matches_df = build_species_match_rows(search_results, query_plan)
    filter_matches_df = build_filter_match_rows(articles_df, filter_config_path=filter_config_path)

    for path, df in [
        (paths["filtered_species_matches_path"], species_matches_df),
        (paths["filtered_filter_matches_path"], filter_matches_df),
        (paths["release_abstracts_path"], articles_df),
        (paths["release_species_matches_path"], species_matches_df),
        (paths["release_filter_matches_path"], filter_matches_df),
    ]:
        ensure_parent(path)
        df.to_parquet(path, index=False)

    source = config["source"]
    metadata = {
        "source": source["name"],
        "query_plan": {
            "path": str(_query_plan_path(config)),
            "approval": query_plan.get("approval", {}),
            "query_count": len(query_plan.get("queries", [])),
        },
        "record_counts": {
            "search_result_rows": len(search_results),
            "unique_pmids": len({str(pmid) for result in search_results for pmid in result.get("pmids", [])}),
            "raw_article_records": len(article_records),
            "release_abstracts": len(articles_df),
            "species_match_rows": len(species_matches_df),
            "filter_match_rows": len(filter_matches_df),
        },
        "ncbi": {
            "config_path": source["ncbi_config_path"],
            "api_key_configured": None,
            "tool": None,
            "rate_limit_without_api_key_per_second": source["rate_limit_without_api_key_per_second"],
            "rate_limit_with_api_key_per_second": source["rate_limit_with_api_key_per_second"],
            "request_delay_seconds": source["request_delay_seconds"],
        },
        "outputs": {key: str(path) for key, path in paths.items()},
    }
    write_json(paths["metadata_path"], metadata)
    return metadata


def run_phase2(
    template_config_path: str | Path = "configs/pubmed_query_templates.yaml",
    filter_config_path: str | Path = "configs/abstract_filters.yaml",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Run complete Phase 2 PubMed query-plan/acquisition/export pipeline."""
    config = load_pubmed_query_template_config(template_config_path)
    plan_result = materialize_pubmed_query_plan(template_config_path, filter_config_path)
    query_plan = load_pubmed_query_plan(plan_result["path"])
    paths = _output_paths(config)

    if paths["raw_search_results_path"].exists() and not force_refresh:
        search_results = read_jsonl(paths["raw_search_results_path"])
    else:
        ncbi_config = load_ncbi_entrez_config(config["source"]["ncbi_config_path"])
        client = PubMedEntrezClient(
            config=ncbi_config,
            request_delay_seconds=float(config["source"]["request_delay_seconds"]),
            max_retries=int(config["source"]["max_retries"]),
            retmax=int(config["source"]["retmax"]),
        )
        search_results = run_pubmed_searches(query_plan, client)

    if paths["raw_article_records_path"].exists() and not force_refresh:
        article_records = read_jsonl(paths["raw_article_records_path"])
    else:
        ncbi_config = load_ncbi_entrez_config(config["source"]["ncbi_config_path"])
        client = PubMedEntrezClient(
            config=ncbi_config,
            request_delay_seconds=float(config["source"]["request_delay_seconds"]),
            max_retries=int(config["source"]["max_retries"]),
            retmax=int(config["source"]["retmax"]),
        )
        article_records = run_pubmed_fetches(search_results, client)

    return write_phase2_outputs(query_plan, search_results, article_records, template_config_path, filter_config_path)
