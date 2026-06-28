"""PubMed E-utilities client for Phase 2 acquisition."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from Bio import Entrez, Medline

from pathogen_atlas_v2.common.io import load_yaml, resolve_path


class PubMedClientError(RuntimeError):
    """Raised when PubMed client configuration or requests fail."""


@dataclass(frozen=True)
class NcbiEntrezConfig:
    """Local NCBI Entrez configuration."""

    email: str
    api_key: str | None
    tool: str


def load_ncbi_entrez_config(path: str | Path) -> NcbiEntrezConfig:
    """Load ignored local NCBI Entrez configuration."""
    path = resolve_path(path)
    if not path.exists():
        raise PubMedClientError(
            f"Missing NCBI Entrez config: {path}. Create configs/local/ncbi_entrez.yaml."
        )
    data = load_yaml(path)
    if not isinstance(data, dict) or not isinstance(data.get("ncbi"), dict):
        raise PubMedClientError(f"NCBI config {path} must contain a top-level ncbi mapping.")
    ncbi = data["ncbi"]
    email = ncbi.get("email")
    if not email:
        raise PubMedClientError(f"NCBI config {path} must define ncbi.email.")
    api_key = ncbi.get("api_key") or None
    return NcbiEntrezConfig(email=str(email), api_key=str(api_key) if api_key else None, tool=str(ncbi.get("tool", "pathogen-atlas-v2")))


class PubMedEntrezClient:
    """Small Entrez client with retry and conservative rate limiting."""

    def __init__(self, config: NcbiEntrezConfig, request_delay_seconds: float, max_retries: int, retmax: int):
        self.config = config
        self.request_delay_seconds = request_delay_seconds
        self.max_retries = max_retries
        self.retmax = retmax
        Entrez.email = config.email
        Entrez.tool = config.tool
        if config.api_key:
            Entrez.api_key = config.api_key

    def _sleep(self) -> None:
        time.sleep(self.request_delay_seconds)

    def _with_retries(self, func, *args: Any, **kwargs: Any) -> Any:
        wait = self.request_delay_seconds
        for attempt in range(self.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                self._sleep()
                return result
            except Exception:
                if attempt >= self.max_retries:
                    raise
                time.sleep(wait)
                wait *= 2

    def search_pmids(self, query: str) -> list[str]:
        """Search PubMed and return all PMIDs for a query."""
        pmids: list[str] = []
        retstart = 0
        total_count: int | None = None
        while total_count is None or retstart < total_count:
            batch = self._search_pmid_batch(query, retstart=retstart)
            total_count = batch["count"]
            pmids.extend(batch["pmids"])
            if not batch["pmids"] or len(batch["pmids"]) < self.retmax:
                break
            retstart += self.retmax
        return list(dict.fromkeys(pmids))

    def _search_pmid_batch(self, query: str, retstart: int) -> dict[str, Any]:
        """Search one PubMed PMID page."""
        def read_search_page() -> dict[str, Any]:
            handle = Entrez.esearch(
                db="pubmed",
                term=query,
                retmode="xml",
                retmax=self.retmax,
                retstart=retstart,
            )
            try:
                return Entrez.read(handle)
            finally:
                handle.close()

        record = self._with_retries(read_search_page)
        return {
            "count": int(record.get("Count", len(record.get("IdList", [])))),
            "pmids": [str(pmid) for pmid in record.get("IdList", [])],
        }

    def fetch_medline_records(self, pmids: Iterable[str]) -> list[dict[str, Any]]:
        """Fetch MEDLINE records for PMIDs."""
        ids = [str(pmid) for pmid in pmids if str(pmid).strip()]
        if not ids:
            return []
        handle = self._with_retries(
            Entrez.efetch,
            db="pubmed",
            id=",".join(ids),
            rettype="medline",
            retmode="text",
        )
        try:
            return [dict(record) for record in Medline.parse(handle)]
        finally:
            handle.close()
