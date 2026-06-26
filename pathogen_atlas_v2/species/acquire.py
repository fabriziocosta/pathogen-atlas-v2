"""Acquire species records from LPSN or an existing raw-record cache."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from pathogen_atlas_v2.common.io import load_yaml, read_jsonl, resolve_path, write_jsonl


class SpeciesAcquisitionError(RuntimeError):
    """Raised when species acquisition cannot proceed."""


@dataclass(frozen=True)
class LpsnCredentials:
    """Credentials for the LPSN API."""

    username: str
    password: str


@dataclass(frozen=True)
class LpsnSourceConfig:
    """Configuration for the Phase 1 LPSN source."""

    name: str
    category: str
    wait_time_seconds: float
    max_retries: int
    credentials_path: Path
    raw_records_path: Path
    normalised_records_path: Path
    release: dict[str, Path]
    v1_reference: dict[str, Path]


def utc_now_iso() -> str:
    """Return a UTC timestamp suitable for metadata."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def retry(max_retries: int, initial_wait: float = 1.0, backoff_factor: float = 2.0) -> Callable:
    """Retry transient API calls with exponential backoff."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            wait = initial_wait
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt >= max_retries:
                        raise
                    message = str(exc)
                    if len(message) > 240:
                        message = message[:240] + "..."
                    print(f"{func.__name__} failed: {message}. Retrying in {wait:.1f}s.")
                    time.sleep(wait)
                    wait *= backoff_factor

        return wrapper

    return decorator


def load_species_source_config(path: str | Path = "configs/species_sources.yaml") -> LpsnSourceConfig:
    """Load the first LPSN source from the species source config."""
    config_path = resolve_path(path)
    config = load_yaml(config_path)
    sources = config.get("sources", [])
    source = next((item for item in sources if item.get("name") == "lpsn"), None)
    if source is None:
        raise SpeciesAcquisitionError(f"No LPSN source configured in {config_path}.")

    release = {
        key: resolve_path(value)
        for key, value in source.get("release", {}).items()
    }
    v1_reference = {
        key: resolve_path(value)
        for key, value in source.get("v1_reference", {}).items()
    }
    return LpsnSourceConfig(
        name=source["name"],
        category=source.get("category", "species"),
        wait_time_seconds=float(source.get("wait_time_seconds", 3)),
        max_retries=int(source.get("max_retries", 5)),
        credentials_path=resolve_path(source["credentials_path"]),
        raw_records_path=resolve_path(source["raw_records_path"]),
        normalised_records_path=resolve_path(source["normalised_records_path"]),
        release=release,
        v1_reference=v1_reference,
    )


def load_lpsn_credentials(path: str | Path) -> LpsnCredentials:
    """Load local LPSN credentials from an ignored YAML file."""
    path = Path(path)
    if not path.exists():
        raise SpeciesAcquisitionError(
            f"Missing LPSN credentials file: {path}. "
            "Create configs/local/lpsn_credentials.yaml with lpsn.username and lpsn.password."
        )
    try:
        data = load_yaml(path)
    except Exception as exc:
        raise SpeciesAcquisitionError(f"Could not read LPSN credentials file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SpeciesAcquisitionError(f"Credentials file {path} must contain a top-level 'lpsn' mapping.")
    lpsn_data = data.get("lpsn")
    if not isinstance(lpsn_data, dict):
        raise SpeciesAcquisitionError(f"Credentials file {path} must contain a top-level 'lpsn' mapping.")
    username = lpsn_data.get("username")
    password = lpsn_data.get("password")
    if not username or not password:
        raise SpeciesAcquisitionError(f"Credentials file {path} must define lpsn.username and lpsn.password.")
    return LpsnCredentials(username=str(username), password=str(password))


class LpsnClientWrapper:
    """Small LPSN API wrapper with pagination, retry, and parent-record caching."""

    def __init__(self, credentials: LpsnCredentials, wait_time_seconds: float = 3, max_retries: int = 5):
        try:
            import lpsn
        except ImportError as exc:
            raise SpeciesAcquisitionError(
                "The 'lpsn' package is required for live acquisition. Install project dependencies first."
            ) from exc
        self.client = lpsn.LpsnClient(credentials.username, credentials.password)
        self.wait_time_seconds = wait_time_seconds
        self.max_retries = max_retries
        self._record_cache: dict[Any, dict[str, Any]] = {}

    def _search(self, **kwargs: Any) -> Any:
        @retry(max_retries=self.max_retries)
        def call() -> Any:
            return self.client.search(**kwargs)

        result = call()
        time.sleep(self.wait_time_seconds)
        return result

    def search_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Return all paginated search results."""
        count = self._search(**kwargs)
        if not count:
            return []

        @retry(max_retries=self.max_retries)
        def call() -> list[dict[str, Any]]:
            return [record for record in self.client.retrieve() if isinstance(record, dict)]

        records = call()
        time.sleep(self.wait_time_seconds)
        return records

    def get_entry_by_id(self, taxon_id: Any) -> dict[str, Any] | None:
        """Retrieve a single LPSN record by ID, using an in-memory cache."""
        if not taxon_id:
            return None
        if taxon_id in self._record_cache:
            return self._record_cache[taxon_id]
        results = self.search_all(id=taxon_id)
        if not results:
            return None
        record = results[0]
        self._record_cache[taxon_id] = record
        return record

    def reconstruct_hierarchy(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Follow LPSN parent IDs from a record up to the top rank."""
        hierarchy: list[dict[str, Any]] = []
        current: dict[str, Any] | None = record
        seen: set[Any] = set()
        while current:
            record_id = current.get("id") or current.get("record_no") or current.get("lpsn_taxon_id")
            if record_id in seen:
                break
            seen.add(record_id)
            hierarchy.append(current)
            parent_id = current.get("lpsn_parent_id")
            if not parent_id:
                break
            current = self.get_entry_by_id(parent_id)
        return hierarchy

    def enrich_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Attach hierarchy and accepted-record details needed by normalisation."""
        enriched = dict(record)
        enriched["_pathogen_atlas_hierarchy"] = self.reconstruct_hierarchy(record)
        status = str(record.get("lpsn_taxonomic_status", "")).lower()
        correct_id = record.get("lpsn_correct_name_id")
        if "synonym" in status and correct_id:
            accepted = self.get_entry_by_id(correct_id)
            if accepted:
                enriched["_pathogen_atlas_accepted_record"] = accepted
                enriched["_pathogen_atlas_accepted_hierarchy"] = self.reconstruct_hierarchy(accepted)
        return enriched


def acquire_lpsn_species_records(config: LpsnSourceConfig, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Acquire LPSN species records, using the raw JSONL cache unless refreshed."""
    if config.raw_records_path.exists() and not force_refresh:
        return read_jsonl(config.raw_records_path)

    credentials = load_lpsn_credentials(config.credentials_path)
    client = LpsnClientWrapper(
        credentials=credentials,
        wait_time_seconds=config.wait_time_seconds,
        max_retries=config.max_retries,
    )
    records = client.search_all(taxon_name="", category=config.category)
    enriched_records = [client.enrich_record(record) for record in records]
    write_jsonl(config.raw_records_path, enriched_records)
    return enriched_records


def main() -> None:
    """Acquire LPSN species records into the raw cache."""
    config = load_species_source_config()
    acquire_lpsn_species_records(config=config, force_refresh=False)
