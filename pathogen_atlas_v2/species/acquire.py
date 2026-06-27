"""Acquire species records from LPSN or an existing raw-record cache."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable

from pathogen_atlas_v2.common.io import ensure_parent, load_yaml, read_jsonl, resolve_path, write_json, write_jsonl


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
    candidate_records_path: Path
    raw_records_path: Path
    lookup_cache_path: Path
    progress_path: Path
    progress_log_every: int
    enrich_taxonomy_hierarchy: bool
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
    raw_records_path = resolve_path(source["raw_records_path"])
    return LpsnSourceConfig(
        name=source["name"],
        category=source.get("category", "species"),
        wait_time_seconds=float(source.get("wait_time_seconds", 3)),
        max_retries=int(source.get("max_retries", 5)),
        credentials_path=resolve_path(source["credentials_path"]),
        candidate_records_path=resolve_path(source["candidate_records_path"]) if source.get("candidate_records_path") else raw_records_path.with_name("lpsn_species_candidates.jsonl"),
        raw_records_path=raw_records_path,
        lookup_cache_path=resolve_path(source["lookup_cache_path"]) if source.get("lookup_cache_path") else raw_records_path.with_name("lpsn_lookup_cache.jsonl"),
        progress_path=resolve_path(source["progress_path"]) if source.get("progress_path") else raw_records_path.with_name("lpsn_species_progress.json"),
        progress_log_every=int(source.get("progress_log_every", 100)),
        enrich_taxonomy_hierarchy=bool(source.get("enrich_taxonomy_hierarchy", False)),
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

    def __init__(
        self,
        credentials: LpsnCredentials,
        wait_time_seconds: float = 3,
        max_retries: int = 5,
        lookup_cache_path: str | Path | None = None,
    ):
        self.credentials = credentials
        self._lpsn_module = self._load_lpsn_module()
        self.client = self._new_client()
        self.wait_time_seconds = wait_time_seconds
        self.max_retries = max_retries
        self.lookup_cache_path = Path(lookup_cache_path) if lookup_cache_path else None
        self._record_cache: dict[str, dict[str, Any]] = {}
        if self.lookup_cache_path and self.lookup_cache_path.exists():
            for record in read_jsonl(self.lookup_cache_path):
                cache_id = record.get("_pathogen_atlas_cache_id") or record_identity(record)
                self._record_cache[str(cache_id)] = record

    def _load_lpsn_module(self) -> Any:
        """Load the LPSN package."""
        try:
            import lpsn
        except ImportError as exc:
            raise SpeciesAcquisitionError(
                "The 'lpsn' package is required for live acquisition. Install project dependencies first."
            ) from exc
        return lpsn

    def _new_client(self) -> Any:
        """Create a fresh authenticated LPSN client."""
        return self._lpsn_module.LpsnClient(self.credentials.username, self.credentials.password)

    def _reauthenticate(self) -> None:
        """Replace the LPSN client after token expiry or invalid refresh-token errors."""
        self.client = self._new_client()

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        """Return True for LPSN/Keycloak token failures that can be fixed by re-login."""
        text = f"{type(exc).__name__}: {exc}".casefold()
        return any(
            marker in text
            for marker in (
                "keycloak",
                "invalid_grant",
                "token is not active",
                "expired token",
                "unauthorized",
            )
        )

    def _search(self, **kwargs: Any) -> Any:
        @retry(max_retries=self.max_retries)
        def call() -> Any:
            try:
                return self.client.search(**kwargs)
            except Exception as exc:
                if self._is_auth_error(exc):
                    self._reauthenticate()
                    return self.client.search(**kwargs)
                raise

        result = call()
        time.sleep(self.wait_time_seconds)
        return result

    def search_count(self, **kwargs: Any) -> int:
        """Run an LPSN search and return the result count."""
        return int(self._search(**kwargs) or 0)

    def iter_search_results(self, **kwargs: Any) -> Iterable[dict[str, Any]]:
        """Yield all records for an LPSN search."""
        records = self.search_all(**kwargs)
        yield from records
        time.sleep(self.wait_time_seconds)

    def search_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Return all paginated search results with auth-refresh recovery."""
        @retry(max_retries=self.max_retries)
        def call() -> list[dict[str, Any]]:
            try:
                count = self.search_count(**kwargs)
                if not count:
                    return []
                return [record for record in self.client.retrieve() if isinstance(record, dict)]
            except Exception as exc:
                if self._is_auth_error(exc):
                    self._reauthenticate()
                raise

        return call()

    def get_entry_by_id(self, taxon_id: Any) -> dict[str, Any] | None:
        """Retrieve a single LPSN record by ID, using an in-memory cache."""
        if not taxon_id:
            return None
        cache_id = str(taxon_id)
        if cache_id in self._record_cache:
            return self._record_cache[cache_id]
        results = self.search_all(id=taxon_id)
        if not results:
            return None
        record = results[0]
        cache_record = {**record, "_pathogen_atlas_cache_id": cache_id}
        self._record_cache[cache_id] = cache_record
        if self.lookup_cache_path:
            append_jsonl(self.lookup_cache_path, cache_record)
        return record

    def get_entry_by_id_local_first(
        self,
        taxon_id: Any,
        local_records_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve an LPSN record by ID, preferring downloaded local records."""
        if not taxon_id:
            return None
        cache_id = str(taxon_id)
        if local_records_by_id and cache_id in local_records_by_id:
            return local_records_by_id[cache_id]
        return self.get_entry_by_id(cache_id)

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

    def enrich_record(
        self,
        record: dict[str, Any],
        enrich_taxonomy_hierarchy: bool = False,
        local_records_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Attach hierarchy and accepted-record details needed by normalisation."""
        enriched = dict(record)
        if enrich_taxonomy_hierarchy:
            enriched["_pathogen_atlas_hierarchy"] = self.reconstruct_hierarchy(record)
        status = str(record.get("lpsn_taxonomic_status", "")).lower()
        correct_id = record.get("lpsn_correct_name_id")
        if "synonym" in status and correct_id:
            accepted = self.get_entry_by_id_local_first(correct_id, local_records_by_id=local_records_by_id)
            if accepted:
                enriched["_pathogen_atlas_accepted_record"] = accepted
                if enrich_taxonomy_hierarchy:
                    enriched["_pathogen_atlas_accepted_hierarchy"] = self.reconstruct_hierarchy(accepted)
        return enriched


def record_identity(record: dict[str, Any]) -> str:
    """Return a stable identity for resume bookkeeping."""
    for key in ("id", "record_no", "lpsn_taxon_id", "lpsn_id"):
        if record.get(key) not in (None, ""):
            return str(record[key])
    for key in ("full_name", "taxon_name", "name"):
        if record.get(key):
            return str(record[key])
    return str(hash(str(sorted(record.items()))))


def records_by_lpsn_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index local records by likely LPSN IDs."""
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        for key in ("id", "record_no", "lpsn_taxon_id", "lpsn_id"):
            value = record.get(key)
            if value not in (None, ""):
                indexed[str(value)] = record
    return indexed


def append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    """Append a single JSONL record."""
    import json

    path = ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.write("\n")


def load_progress(path: str | Path) -> dict[str, Any]:
    """Load Phase 1 progress if present."""
    import json

    path = Path(path)
    if not path.exists():
        return {"status": "not_started"}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_progress(path: str | Path, **values: Any) -> dict[str, Any]:
    """Write and return Phase 1 progress metadata."""
    progress = {**load_progress(path), **values, "updated_at": utc_now_iso()}
    write_json(path, progress)
    return progress


def log_progress(message: str, enabled: bool = True) -> None:
    """Emit notebook-friendly progress logs."""
    if enabled:
        print(message, flush=True)


def acquire_lpsn_species_records(
    config: LpsnSourceConfig,
    force_refresh: bool = False,
    resume: bool = True,
    log: bool = True,
) -> list[dict[str, Any]]:
    """Acquire LPSN species records, supporting live progress and resume."""
    if config.raw_records_path.exists() and not force_refresh:
        progress = load_progress(config.progress_path)
        if not resume or progress.get("status") not in {"retrieving", "resolving_synonyms", "enriching", "running"}:
            log_progress(f"Phase 1 acquisition: using cached raw records at {config.raw_records_path}.", log)
            return read_jsonl(config.raw_records_path)

    if force_refresh:
        for path in (config.candidate_records_path, config.raw_records_path, config.progress_path, config.lookup_cache_path):
            if path.exists():
                path.unlink()

    credentials = load_lpsn_credentials(config.credentials_path)
    client = LpsnClientWrapper(
        credentials=credentials,
        wait_time_seconds=config.wait_time_seconds,
        max_retries=config.max_retries,
        lookup_cache_path=config.lookup_cache_path,
    )
    existing_records = read_jsonl(config.raw_records_path) if config.raw_records_path.exists() else []
    completed_ids = {record_identity(record) for record in existing_records}
    candidate_records = read_jsonl(config.candidate_records_path) if config.candidate_records_path.exists() else []
    candidate_ids = {record_identity(record) for record in candidate_records}

    progress = load_progress(config.progress_path)
    total = int(progress["total_records"]) if progress.get("total_records") else 0
    if not total or len(candidate_records) < total:
        total = client.search_count(taxon_name="", category=config.category)
    write_progress(
        config.progress_path,
        status="retrieving" if len(candidate_records) < total else "resolving_synonyms",
        mode="force_refresh" if force_refresh else "resume",
        enrich_taxonomy_hierarchy=config.enrich_taxonomy_hierarchy,
        total_records=total,
        candidate_records=len(candidate_records),
        retrieved_records=len(candidate_records),
        completed_records=len(completed_ids),
        remaining_records=max(total - len(completed_ids), 0),
        candidate_records_path=str(config.candidate_records_path),
        raw_records_path=str(config.raw_records_path),
    )
    log_progress(
        f"Phase 1 acquisition: {len(candidate_records)}/{total} candidate records retrieved; "
        f"{len(completed_ids)}/{total} records resolved.",
        log,
    )

    if len(candidate_records) < total:
        while len(candidate_records) < total:
            before_pass = len(candidate_records)
            try:
                for record in client.client.retrieve():
                    if not isinstance(record, dict):
                        continue
                    record_id = record_identity(record)
                    if record_id in candidate_ids:
                        continue
                    append_jsonl(config.candidate_records_path, record)
                    candidate_records.append(record)
                    candidate_ids.add(record_id)
                    if len(candidate_records) == 1 or len(candidate_records) % config.progress_log_every == 0 or len(candidate_records) >= total:
                        write_progress(
                            config.progress_path,
                            status="retrieving",
                            mode="force_refresh" if force_refresh else "resume",
                            enrich_taxonomy_hierarchy=config.enrich_taxonomy_hierarchy,
                            total_records=total,
                            candidate_records=len(candidate_records),
                            retrieved_records=len(candidate_records),
                            completed_records=len(completed_ids),
                            remaining_records=max(total - len(completed_ids), 0),
                            candidate_records_path=str(config.candidate_records_path),
                            raw_records_path=str(config.raw_records_path),
                        )
                        log_progress(
                            f"Phase 1 retrieval: {len(candidate_records)}/{total} candidate records saved.",
                            log,
                        )
            except Exception as exc:
                if not client._is_auth_error(exc):
                    raise
                log_progress(
                    "Phase 1 retrieval: LPSN token expired; re-authenticating and resuming from saved candidates.",
                    log,
                )
                client._reauthenticate()
                total = client.search_count(taxon_name="", category=config.category)
                continue
            if len(candidate_records) == before_pass:
                break
        if len(candidate_records) < total:
            log_progress(
                f"Phase 1 retrieval stopped with {len(candidate_records)}/{total} candidate records saved. "
                "Rerun with RESUME=True to continue.",
                log,
            )

    write_progress(
        config.progress_path,
        status="resolving_synonyms",
        mode="force_refresh" if force_refresh else "resume",
        enrich_taxonomy_hierarchy=config.enrich_taxonomy_hierarchy,
        total_records=total,
        candidate_records=len(candidate_records),
        retrieved_records=len(candidate_records),
        completed_records=len(completed_ids),
        remaining_records=max(total - len(completed_ids), 0),
        candidate_records_path=str(config.candidate_records_path),
        raw_records_path=str(config.raw_records_path),
    )

    local_records_by_id = records_by_lpsn_id(candidate_records)
    log_progress(
        f"Phase 1 synonym resolution: using {len(local_records_by_id)} local LPSN records before API fallback.",
        log,
    )
    for record in candidate_records:
        record_id = record_identity(record)
        if record_id in completed_ids:
            continue
        enriched = client.enrich_record(
            record,
            enrich_taxonomy_hierarchy=config.enrich_taxonomy_hierarchy,
            local_records_by_id=local_records_by_id,
        )
        append_jsonl(config.raw_records_path, enriched)
        existing_records.append(enriched)
        completed_ids.add(record_id)
        if len(completed_ids) == 1 or len(completed_ids) % config.progress_log_every == 0 or len(completed_ids) >= total:
            write_progress(
                config.progress_path,
                status="resolving_synonyms",
                mode="force_refresh" if force_refresh else "resume",
                enrich_taxonomy_hierarchy=config.enrich_taxonomy_hierarchy,
                total_records=total,
                candidate_records=len(candidate_records),
                retrieved_records=len(candidate_records),
                completed_records=len(completed_ids),
                remaining_records=max(total - len(completed_ids), 0),
                candidate_records_path=str(config.candidate_records_path),
                raw_records_path=str(config.raw_records_path),
            )
            log_progress(
                f"Phase 1 synonym resolution: {len(completed_ids)}/{total} records saved; "
                f"{max(total - len(completed_ids), 0)} remaining.",
                log,
            )

    write_progress(
        config.progress_path,
        status="completed",
        mode="force_refresh" if force_refresh else "resume",
        enrich_taxonomy_hierarchy=config.enrich_taxonomy_hierarchy,
        total_records=total,
        candidate_records=len(candidate_records),
        retrieved_records=len(candidate_records),
        completed_records=len(completed_ids),
        remaining_records=0,
        candidate_records_path=str(config.candidate_records_path),
        raw_records_path=str(config.raw_records_path),
    )
    log_progress(f"Phase 1 acquisition: completed {len(completed_ids)}/{total} records.", log)
    return existing_records


def main() -> None:
    """Acquire LPSN species records into the raw cache."""
    config = load_species_source_config()
    acquire_lpsn_species_records(config=config, force_refresh=False)
