"""Record abstract filter-term matches for PubMed articles."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from pathogen_atlas_v2.abstracts.pubmed_query_templates import load_abstract_filter_terms


FILTER_MATCH_COLUMNS = ["pmid", "filter_family", "term", "field", "source_column"]


FIELD_TO_COLUMNS = {
    "Title/Abstract": ["title", "abstract"],
    "Keywords": ["keywords"],
    "MeSH Terms": ["mesh_terms"],
}


def _field_text(row: pd.Series, column: str) -> str:
    value = row.get(column, "")
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _contains_term(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.IGNORECASE) is not None


def build_filter_match_rows(
    articles_df: pd.DataFrame,
    filter_config_path: str = "configs/abstract_filters.yaml",
) -> pd.DataFrame:
    """Build one row per PMID/filter-term/field match."""
    terms = load_abstract_filter_terms(filter_config_path)
    rows: list[dict[str, Any]] = []
    for article in articles_df.itertuples(index=False):
        row = pd.Series(article._asdict())
        for field, columns in FIELD_TO_COLUMNS.items():
            for column in columns:
                text = _field_text(row, column)
                for family, family_terms in terms.items():
                    for term in family_terms:
                        if _contains_term(text, term):
                            rows.append(
                                {
                                    "pmid": str(row["pmid"]),
                                    "filter_family": family,
                                    "term": term,
                                    "field": field,
                                    "source_column": column,
                                }
                            )
    df = pd.DataFrame(rows, columns=FILTER_MATCH_COLUMNS)
    if df.empty:
        return df
    return df.drop_duplicates().sort_values(["pmid", "filter_family", "field", "term"]).reset_index(drop=True)
