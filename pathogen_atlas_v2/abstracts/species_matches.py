"""Build PMID-to-species evidence rows from query results."""

from __future__ import annotations

from typing import Any

import pandas as pd


SPECIES_MATCH_COLUMNS = [
    "pmid",
    "canonical_species",
    "matched_name",
    "mapping_type",
    "query_id",
    "query_hash",
    "template_id",
    "field",
    "query",
]


def build_species_match_rows(search_results: list[dict[str, Any]], query_plan: dict[str, Any]) -> pd.DataFrame:
    """Build many-to-many PMID/species/query evidence rows."""
    query_by_id = {query["query_id"]: query for query in query_plan.get("queries", [])}
    rows: list[dict[str, Any]] = []
    for result in search_results:
        query = query_by_id.get(result["query_id"])
        if not query:
            continue
        for pmid in result.get("pmids", []):
            rows.append(
                {
                    "pmid": str(pmid),
                    "canonical_species": query["canonical_species"],
                    "matched_name": query["matched_name"],
                    "mapping_type": query["mapping_type"],
                    "query_id": query["query_id"],
                    "query_hash": query["query_hash"],
                    "template_id": query["template_id"],
                    "field": query["field"],
                    "query": query["query"],
                }
            )
    df = pd.DataFrame(rows, columns=SPECIES_MATCH_COLUMNS)
    if df.empty:
        return df
    return df.drop_duplicates().sort_values(["pmid", "canonical_species", "matched_name", "field"]).reset_index(drop=True)
