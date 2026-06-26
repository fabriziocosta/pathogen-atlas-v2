"""Build PMID-to-species evidence rows from query results."""

from __future__ import annotations

from typing import Any

import pandas as pd


SPECIES_MATCH_COLUMNS = [
    "pmid",
    "canonical_species",
    "matched_names",
    "mapping_types",
    "synonym_count",
    "query_id",
    "query_hash",
    "template_id",
    "field",
    "query",
    "selected_for_fetch",
]


def build_species_match_rows(search_results: list[dict[str, Any]], query_plan: dict[str, Any]) -> pd.DataFrame:
    """Build many-to-many PMID/species/query evidence rows."""
    query_by_id = {query["query_id"]: query for query in query_plan.get("queries", [])}
    rows: list[dict[str, Any]] = []
    for result in search_results:
        query = query_by_id.get(result["query_id"])
        if not query:
            continue
        selected_pmids = {str(pmid) for pmid in result.get("sampled_pmids", result.get("pmids", []))}
        for pmid in result.get("pmids", []):
            rows.append(
                {
                    "pmid": str(pmid),
                    "canonical_species": query["canonical_species"],
                    "matched_names": query["matched_names"],
                    "mapping_types": query["mapping_types"],
                    "synonym_count": query["synonym_count"],
                    "query_id": query["query_id"],
                    "query_hash": query["query_hash"],
                    "template_id": query["template_id"],
                    "field": query["field"],
                    "query": query["query"],
                    "selected_for_fetch": str(pmid) in selected_pmids,
                }
            )
    df = pd.DataFrame(rows, columns=SPECIES_MATCH_COLUMNS)
    if df.empty:
        return df
    return df.drop_duplicates(subset=["pmid", "canonical_species", "query_id"]).sort_values(["pmid", "canonical_species", "field"]).reset_index(drop=True)
