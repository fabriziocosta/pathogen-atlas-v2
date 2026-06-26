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


def _append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    """Append one JSONL record after successful work."""
    import json

    path = ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.write("\n")


def _deduplicate_records(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Keep the last record for each key while preserving first-seen order."""
    order = []
    by_key = {}
    for record in records:
        value = record.get(key)
        if value is None:
            continue
        if value not in by_key:
            order.append(value)
        by_key[value] = record
    return [by_key[value] for value in order]


def _build_pubmed_client(config: dict[str, Any]) -> PubMedEntrezClient:
    """Build a configured PubMed client."""
    ncbi_config = load_ncbi_entrez_config(config["source"]["ncbi_config_path"])
    return PubMedEntrezClient(
        config=ncbi_config,
        request_delay_seconds=float(config["source"]["request_delay_seconds"]),
        max_retries=int(config["source"]["max_retries"]),
        retmax=int(config["source"]["retmax"]),
    )


def _log(message: str, enabled: bool) -> None:
    if enabled:
        print(message, flush=True)


def run_pubmed_searches(
    query_plan: dict[str, Any],
    client: PubMedEntrezClient,
    existing_results: list[dict[str, Any]] | None = None,
    output_path: str | Path | None = None,
    log: bool = True,
    log_every: int = 25,
) -> list[dict[str, Any]]:
    """Run PubMed searches for all rendered queries, skipping completed query IDs."""
    results = _deduplicate_records(existing_results or [], "query_id")
    completed = {result["query_id"] for result in results}
    queries = query_plan.get("queries", [])
    pending = [query for query in queries if query["query_id"] not in completed]
    _log(f"PMID search: {len(completed)}/{len(queries)} queries already complete; {len(pending)} remaining.", log)
    for offset, query in enumerate(pending, start=1):
        pmids = client.search_pmids(query["query"])
        record = {"query_id": query["query_id"], "query_hash": query["query_hash"], "pmids": pmids}
        results.append(record)
        completed.add(query["query_id"])
        if output_path:
            _append_jsonl(output_path, record)
        if offset == 1 or offset == len(pending) or offset % log_every == 0:
            _log(
                f"PMID search: {len(completed)}/{len(queries)} queries complete; "
                f"{len(queries) - len(completed)} remaining.",
                log,
            )
    return _deduplicate_records(results, "query_id")


def build_canonical_pmid_index(search_results: list[dict[str, Any]], query_plan: dict[str, Any]) -> pd.DataFrame:
    """Build one row per canonical species with all PMIDs found by its queries."""
    query_by_id = {query["query_id"]: query for query in query_plan.get("queries", [])}
    species_to_pmids: dict[str, set[str]] = {}
    species_to_queries: dict[str, set[str]] = {}
    species_to_synonyms: dict[str, set[str]] = {}
    for result in search_results:
        query = query_by_id.get(result["query_id"])
        if not query:
            continue
        canonical_species = query["canonical_species"]
        species_to_pmids.setdefault(canonical_species, set()).update(str(pmid) for pmid in result.get("pmids", []))
        species_to_queries.setdefault(canonical_species, set()).add(query["query_id"])
        species_to_synonyms.setdefault(canonical_species, set()).update(query.get("matched_names", []))
    rows = []
    for canonical_species in sorted(species_to_pmids):
        pmids = sorted(species_to_pmids[canonical_species])
        rows.append(
            {
                "canonical_species": canonical_species,
                "pmids": pmids,
                "n_pmids": len(pmids),
                "query_ids": sorted(species_to_queries.get(canonical_species, set())),
                "matched_names": sorted(species_to_synonyms.get(canonical_species, set()), key=str.casefold),
            }
        )
    return pd.DataFrame(rows, columns=["canonical_species", "pmids", "n_pmids", "query_ids", "matched_names"])


def sample_canonical_pmids(
    canonical_pmid_df: pd.DataFrame,
    max_pmids_per_canonical_species: int,
    oldest_pmids_per_canonical_species: int,
    random_seed: int,
) -> pd.DataFrame:
    """Keep oldest PMIDs first, then uniformly sample remaining PMIDs without duplicates."""
    rows = []
    for row in canonical_pmid_df.itertuples(index=False):
        pmids = list(row.pmids)
        oldest_count = min(oldest_pmids_per_canonical_species, max_pmids_per_canonical_species, len(pmids))
        oldest_pmids = sorted(pmids, key=lambda value: int(value) if str(value).isdigit() else str(value))[:oldest_count]
        remaining_pmids = [pmid for pmid in pmids if pmid not in set(oldest_pmids)]
        remaining_budget = max_pmids_per_canonical_species - len(oldest_pmids)
        if len(remaining_pmids) > remaining_budget:
            sampled_remaining = (
                pd.Series(remaining_pmids)
                .sample(n=remaining_budget, random_state=random_seed)
                .tolist()
            )
        else:
            sampled_remaining = sorted(remaining_pmids, key=lambda value: int(value) if str(value).isdigit() else str(value))
        sampled = sorted(set(oldest_pmids + sampled_remaining), key=lambda value: int(value) if str(value).isdigit() else str(value))
        rows.append(
            {
                "canonical_species": row.canonical_species,
                "pmids": sampled,
                "oldest_pmids": oldest_pmids,
                "sampled_remaining_pmids": sorted(sampled_remaining, key=lambda value: int(value) if str(value).isdigit() else str(value)),
                "n_pmids": len(sampled),
                "n_pmids_available": len(pmids),
                "max_pmids_per_canonical_species": max_pmids_per_canonical_species,
                "oldest_pmids_per_canonical_species": oldest_pmids_per_canonical_species,
                "pmid_sample_random_seed": random_seed,
                "matched_names": row.matched_names,
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "canonical_species",
            "pmids",
            "oldest_pmids",
            "sampled_remaining_pmids",
            "n_pmids",
            "n_pmids_available",
            "max_pmids_per_canonical_species",
            "oldest_pmids_per_canonical_species",
            "pmid_sample_random_seed",
            "matched_names",
        ],
    )


def attach_sampled_pmids_to_search_results(
    search_results: list[dict[str, Any]],
    query_plan: dict[str, Any],
    sampled_canonical_pmid_df: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Attach sampled PMIDs to each query result for provenance."""
    query_by_id = {query["query_id"]: query for query in query_plan.get("queries", [])}
    sampled_by_species = {
        row.canonical_species: {str(pmid) for pmid in row.pmids}
        for row in sampled_canonical_pmid_df.itertuples(index=False)
    }
    enriched = []
    for result in search_results:
        query = query_by_id.get(result["query_id"])
        sampled_pmids = sampled_by_species.get(query["canonical_species"], set()) if query else set()
        result_copy = dict(result)
        result_copy["sampled_pmids"] = sorted(set(str(pmid) for pmid in result.get("pmids", [])) & sampled_pmids)
        enriched.append(result_copy)
    return enriched


def selected_pmids_from_search_results(search_results: list[dict[str, Any]]) -> list[str]:
    """Return unique PMIDs selected for abstract fetching."""
    return sorted({str(pmid) for result in search_results for pmid in result.get("sampled_pmids", result.get("pmids", []))})


def pmids_from_article_records(article_records: list[dict[str, Any]]) -> set[str]:
    """Return PMIDs already present in raw article records."""
    return {str(record.get("PMID")) for record in article_records if record.get("PMID")}


def run_pubmed_fetches(
    search_results: list[dict[str, Any]],
    client: PubMedEntrezClient,
    existing_records: list[dict[str, Any]] | None = None,
    output_path: str | Path | None = None,
    batch_size: int = 200,
    log: bool = True,
    log_every: int = 10,
) -> list[dict[str, Any]]:
    """Fetch MEDLINE records for selected PMIDs, skipping PMIDs already saved."""
    article_records = existing_records or []
    target_pmids = selected_pmids_from_search_results(search_results)
    fetched_pmids = pmids_from_article_records(article_records)
    pending_pmids = [pmid for pmid in target_pmids if pmid not in fetched_pmids]
    total = len(target_pmids)
    fetched_target_count = len(set(target_pmids) & fetched_pmids)
    _log(f"Abstract fetch: {fetched_target_count}/{total} PMIDs already fetched; {len(pending_pmids)} remaining.", log)
    if not pending_pmids:
        return article_records
    batches = [pending_pmids[index : index + batch_size] for index in range(0, len(pending_pmids), batch_size)]
    for batch_index, batch_pmids in enumerate(batches, start=1):
        batch_records = client.fetch_medline_records(batch_pmids)
        new_records = [record for record in batch_records if str(record.get("PMID")) not in fetched_pmids]
        article_records.extend(new_records)
        for record in new_records:
            fetched_pmids.add(str(record.get("PMID")))
            if output_path:
                _append_jsonl(output_path, record)
        if batch_index == 1 or batch_index == len(batches) or batch_index % log_every == 0:
            fetched_target_count = len(set(target_pmids) & fetched_pmids)
            _log(
                f"Abstract fetch: {fetched_target_count}/{total} PMIDs fetched; "
                f"{max(total - fetched_target_count, 0)} remaining.",
                log,
            )
    return article_records


def prepare_phase2_search_results(
    query_plan: dict[str, Any],
    search_results: list[dict[str, Any]],
    template_config_path: str | Path = "configs/pubmed_query_templates.yaml",
) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.DataFrame]:
    """Build canonical PMID indexes and attach sampled PMIDs to search results."""
    config = load_pubmed_query_template_config(template_config_path)
    source = config["source"]
    canonical_pmid_df = build_canonical_pmid_index(search_results, query_plan)
    sampled_canonical_pmid_df = sample_canonical_pmids(
        canonical_pmid_df,
        max_pmids_per_canonical_species=int(source.get("max_pmids_per_canonical_species", 200)),
        oldest_pmids_per_canonical_species=int(source.get("oldest_pmids_per_canonical_species", 30)),
        random_seed=int(source.get("pmid_sample_random_seed", 42)),
    )
    enriched_search_results = attach_sampled_pmids_to_search_results(search_results, query_plan, sampled_canonical_pmid_df)
    return enriched_search_results, canonical_pmid_df, sampled_canonical_pmid_df


def write_phase2_intermediate_tables(
    query_plan: dict[str, Any],
    search_results: list[dict[str, Any]],
    template_config_path: str | Path = "configs/pubmed_query_templates.yaml",
) -> dict[str, Any]:
    """Write Phase 2 PMID search outputs and sampled indexes."""
    config = load_pubmed_query_template_config(template_config_path)
    paths = _output_paths(config)
    enriched_search_results, canonical_pmid_df, sampled_canonical_pmid_df = prepare_phase2_search_results(
        query_plan,
        search_results,
        template_config_path,
    )
    write_jsonl(paths["raw_search_results_path"], enriched_search_results)
    for path, df in [
        (paths["canonical_pmid_index_path"], canonical_pmid_df),
        (paths["sampled_canonical_pmid_index_path"], sampled_canonical_pmid_df),
    ]:
        ensure_parent(path)
        df.to_parquet(path, index=False)
    return {
        "search_results": enriched_search_results,
        "canonical_pmid_index": canonical_pmid_df,
        "sampled_canonical_pmid_index": sampled_canonical_pmid_df,
    }


def run_phase2_pmid_acquisition(
    template_config_path: str | Path = "configs/pubmed_query_templates.yaml",
    filter_config_path: str | Path = "configs/abstract_filters.yaml",
    force_refresh: bool = False,
    log: bool = True,
    log_every: int = 25,
) -> dict[str, Any]:
    """Materialize the query plan and acquire all PMID search results resumably."""
    config = load_pubmed_query_template_config(template_config_path)
    plan_result = materialize_pubmed_query_plan(template_config_path, filter_config_path)
    query_plan = load_pubmed_query_plan(plan_result["path"])
    paths = _output_paths(config)

    if force_refresh and paths["raw_search_results_path"].exists():
        paths["raw_search_results_path"].unlink()
    existing_results = read_jsonl(paths["raw_search_results_path"]) if paths["raw_search_results_path"].exists() else []
    client = _build_pubmed_client(config)
    search_results = run_pubmed_searches(
        query_plan,
        client,
        existing_results=existing_results,
        output_path=paths["raw_search_results_path"],
        log=log,
        log_every=log_every,
    )
    tables = write_phase2_intermediate_tables(query_plan, search_results, template_config_path)
    unique_pmids = len({str(pmid) for result in tables["search_results"] for pmid in result.get("pmids", [])})
    sampled_unique_pmids = len({str(pmid) for result in tables["search_results"] for pmid in result.get("sampled_pmids", [])})
    return {
        "status": "completed",
        "query_plan": {"path": plan_result["path"], "query_count": len(query_plan.get("queries", [])), "approval": query_plan.get("approval", {})},
        "record_counts": {
            "search_result_rows": len(tables["search_results"]),
            "unique_pmids": unique_pmids,
            "sampled_unique_pmids": sampled_unique_pmids,
            "canonical_species_with_pmids": len(tables["canonical_pmid_index"]),
        },
        "outputs": {key: str(path) for key, path in paths.items()},
    }


def run_phase2_abstract_fetch(
    template_config_path: str | Path = "configs/pubmed_query_templates.yaml",
    filter_config_path: str | Path = "configs/abstract_filters.yaml",
    force_refresh: bool = False,
    log: bool = True,
    log_every: int = 10,
) -> dict[str, Any]:
    """Fetch abstracts for selected PMIDs resumably and write release tables."""
    config = load_pubmed_query_template_config(template_config_path)
    paths = _output_paths(config)
    query_plan = load_pubmed_query_plan(_query_plan_path(config))
    if not paths["raw_search_results_path"].exists():
        raise PubMedClientError("Missing PubMed search results. Run the PMID acquisition notebook first.")
    search_results = read_jsonl(paths["raw_search_results_path"])
    search_results, _, _ = prepare_phase2_search_results(query_plan, search_results, template_config_path)
    write_jsonl(paths["raw_search_results_path"], search_results)

    if force_refresh and paths["raw_article_records_path"].exists():
        paths["raw_article_records_path"].unlink()
    existing_records = read_jsonl(paths["raw_article_records_path"]) if paths["raw_article_records_path"].exists() else []
    client = _build_pubmed_client(config)
    article_records = run_pubmed_fetches(
        search_results,
        client,
        existing_records=existing_records,
        output_path=paths["raw_article_records_path"],
        batch_size=int(config["source"].get("fetch_batch_size", 200)),
        log=log,
        log_every=log_every,
    )
    return write_phase2_outputs(query_plan, search_results, article_records, template_config_path, filter_config_path)


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

    write_jsonl(paths["raw_article_records_path"], article_records)

    source = config["source"]
    search_results, canonical_pmid_df, sampled_canonical_pmid_df = prepare_phase2_search_results(
        query_plan,
        search_results,
        template_config_path,
    )
    write_jsonl(paths["raw_search_results_path"], search_results)

    articles_df = build_articles_dataframe(article_records)
    species_matches_df = build_species_match_rows(search_results, query_plan)
    filter_matches_df = build_filter_match_rows(articles_df, filter_config_path=filter_config_path)

    for path, df in [
        (paths["canonical_pmid_index_path"], canonical_pmid_df),
        (paths["sampled_canonical_pmid_index_path"], sampled_canonical_pmid_df),
        (paths["filtered_species_matches_path"], species_matches_df),
        (paths["filtered_filter_matches_path"], filter_matches_df),
        (paths["release_abstracts_path"], articles_df),
        (paths["release_species_matches_path"], species_matches_df),
        (paths["release_filter_matches_path"], filter_matches_df),
    ]:
        ensure_parent(path)
        df.to_parquet(path, index=False)

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
            "sampled_unique_pmids": len({str(pmid) for result in search_results for pmid in result.get("sampled_pmids", [])}),
            "raw_article_records": len(article_records),
            "canonical_species_with_pmids": len(canonical_pmid_df),
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
        "sampling": {
            "max_pmids_per_canonical_species": int(source.get("max_pmids_per_canonical_species", 200)),
            "oldest_pmids_per_canonical_species": int(source.get("oldest_pmids_per_canonical_species", 30)),
            "pmid_sample_random_seed": int(source.get("pmid_sample_random_seed", 42)),
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
        client = _build_pubmed_client(config)
        search_results = run_pubmed_searches(query_plan, client, output_path=paths["raw_search_results_path"])

    search_results, _, _ = prepare_phase2_search_results(query_plan, search_results, template_config_path)

    if paths["raw_article_records_path"].exists() and not force_refresh:
        article_records = read_jsonl(paths["raw_article_records_path"])
    else:
        client = _build_pubmed_client(config)
        article_records = run_pubmed_fetches(
            search_results,
            client,
            output_path=paths["raw_article_records_path"],
            batch_size=int(config["source"].get("fetch_batch_size", 200)),
        )

    return write_phase2_outputs(query_plan, search_results, article_records, template_config_path, filter_config_path)
