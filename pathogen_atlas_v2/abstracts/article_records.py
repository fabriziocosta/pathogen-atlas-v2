"""Parse PubMed article records into normalized rows."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd


ARTICLE_COLUMNS = [
    "pmid",
    "title",
    "abstract",
    "publication_date",
    "publication_year",
    "authors",
    "journal",
    "doi",
    "mesh_terms",
    "keywords",
    "source",
]


def extract_publication_year(publication_date: str) -> str:
    """Extract a four-digit publication year from a MEDLINE date string."""
    match = re.search(r"\b(\d{4})\b", publication_date or "")
    return match.group(1) if match else ""


def extract_doi(aid_values: Any) -> str:
    """Extract DOI from MEDLINE AID values."""
    if not aid_values:
        return ""
    values = aid_values if isinstance(aid_values, list) else [aid_values]
    for value in values:
        text = str(value)
        if text.endswith("[doi]"):
            return text.replace("[doi]", "").strip()
    return ""


def parse_pubmed_article_record(record: dict[str, Any], source: str = "pubmed") -> dict[str, Any]:
    """Parse one MEDLINE record dictionary."""
    publication_date = str(record.get("DP", ""))
    return {
        "pmid": str(record.get("PMID", "")),
        "title": str(record.get("TI", "")),
        "abstract": str(record.get("AB", "")),
        "publication_date": publication_date,
        "publication_year": extract_publication_year(publication_date),
        "authors": list(record.get("AU", []) or []),
        "journal": str(record.get("JT", "")),
        "doi": extract_doi(record.get("AID", [])),
        "mesh_terms": list(record.get("MH", []) or []),
        "keywords": list(record.get("OT", []) or []),
        "source": source,
    }


def build_articles_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Build one deduplicated article row per PMID."""
    rows = [parse_pubmed_article_record(record) for record in records]
    df = pd.DataFrame(rows, columns=ARTICLE_COLUMNS)
    if df.empty:
        return df
    df = df[df["pmid"].astype(str).str.len() > 0]
    return df.drop_duplicates(subset=["pmid"]).sort_values("pmid").reset_index(drop=True)
