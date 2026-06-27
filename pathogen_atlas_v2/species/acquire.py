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


TAXONOMY_PREFETCH_CATEGORIES = ("genus", "family", "order", "class", "phylum", "kingdom", "domain")


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

    def reconstruct_hierarchy(
        self,
        record: dict[str, Any],
        local_records_by_id: dict[str, dict[str, Any]] | None = None,
        hierarchy_cache: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        """Follow LPSN parent IDs, preferring downloaded local records."""
        hierarchy_cache = hierarchy_cache if hierarchy_cache is not None else {}
        start_id = record_identity(record)
        if start_id in hierarchy_cache:
            return list(hierarchy_cache[start_id])

        hierarchy: list[dict[str, Any]] = []
        current: dict[str, Any] | None = record
        seen: set[Any] = set()
        while current:
            record_id = record_identity(current)
            if record_id in seen:
                break
            if hierarchy and record_id in hierarchy_cache:
                hierarchy.extend(hierarchy_cache[record_id])
                break
            seen.add(record_id)
            hierarchy.append(current)
            parent_id = current.get("lpsn_parent_id")
            if not parent_id:
                break
            parent_cache_id = str(parent_id)
            if parent_cache_id in hierarchy_cache:
                hierarchy.extend(hierarchy_cache[parent_cache_id])
                break
            current = self.get_entry_by_id_local_first(parent_id, local_records_by_id=local_records_by_id)

        for index, entry in enumerate(hierarchy):
            hierarchy_cache.setdefault(record_identity(entry), list(hierarchy[index:]))
        return hierarchy

    def enrich_record(
        self,
        record: dict[str, Any],
        enrich_taxonomy_hierarchy: bool = False,
        local_records_by_id: dict[str, dict[str, Any]] | None = None,
        hierarchy_cache: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """Attach hierarchy and accepted-record details needed by normalisation."""
        enriched = dict(record)
        if enrich_taxonomy_hierarchy:
            enriched["_pathogen_atlas_hierarchy"] = self.reconstruct_hierarchy(
                record,
                local_records_by_id=local_records_by_id,
                hierarchy_cache=hierarchy_cache,
            )
        correct_id = record.get("lpsn_correct_name_id")
        if should_resolve_correct_name(record):
            accepted = self.get_entry_by_id_local_first(correct_id, local_records_by_id=local_records_by_id)
            if accepted:
                enriched["_pathogen_atlas_accepted_record"] = accepted
                if enrich_taxonomy_hierarchy:
                    enriched["_pathogen_atlas_accepted_hierarchy"] = self.reconstruct_hierarchy(
                        accepted,
                        local_records_by_id=local_records_by_id,
                        hierarchy_cache=hierarchy_cache,
                    )
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


def should_resolve_correct_name(record: dict[str, Any]) -> bool:
    """Return True for records whose LPSN correct-name ID should be followed."""
    correct_id = record.get("lpsn_correct_name_id")
    record_id = record.get("id") or record.get("record_no") or record.get("lpsn_taxon_id") or record.get("lpsn_id")
    if not correct_id or str(correct_id) == str(record_id):
        return False
    status = str(record.get("lpsn_taxonomic_status", "")).casefold()
    nomenclatural_status = str(record.get("nomenclatural_status", "")).casefold()
    return "synonym" in status or "misspelling" in status or "spelling" in nomenclatural_status


def records_missing_taxonomy_hierarchy(records: list[dict[str, Any]]) -> int:
    """Return the number of records missing enriched taxonomy hierarchy."""
    return sum(1 for record in records if "_pathogen_atlas_hierarchy" not in record)


def records_missing_correct_name_resolution(records: list[dict[str, Any]]) -> int:
    """Return records missing cached correct-name details for non-canonical names."""
    return sum(
        1
        for record in records
        if should_resolve_correct_name(record) and "_pathogen_atlas_accepted_record" not in record
    )


def prefetch_taxonomy_records(
    client: LpsnClientWrapper,
    local_records_by_id: dict[str, dict[str, Any]],
    categories: Iterable[str] = TAXONOMY_PREFETCH_CATEGORIES,
    log: bool = True,
) -> int:
    """Fetch taxonomy rank records by category once and add them to local/cache indexes."""
    added = 0
    for category in categories:
        records = client.search_all(taxon_name="", category=category)
        category_added = 0
        for record in records:
            record_id = record_identity(record)
            if record_id in local_records_by_id:
                continue
            cache_record = {**record, "_pathogen_atlas_cache_id": record_id}
            local_records_by_id[record_id] = cache_record
            client._record_cache[record_id] = cache_record
            if client.lookup_cache_path:
                append_jsonl(client.lookup_cache_path, cache_record)
            added += 1
            category_added += 1
        log_progress(
            f"Phase 1 taxonomy prefetch: cached {category_added} new {category} records.",
            log,
        )
    return added


def seed_hierarchy_cache(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Build a hierarchy cache from records that already carry hierarchy."""
    hierarchy_cache: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        for key in ("_pathogen_atlas_hierarchy", "_pathogen_atlas_accepted_hierarchy"):
            hierarchy = record.get(key)
            if not isinstance(hierarchy, list):
                continue
            for index, entry in enumerate(hierarchy):
                if isinstance(entry, dict):
                    hierarchy_cache.setdefault(record_identity(entry), list(hierarchy[index:]))
    return hierarchy_cache


def backfill_taxonomy_hierarchy(
    config: LpsnSourceConfig,
    client: LpsnClientWrapper,
    records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    log: bool = True,
) -> list[dict[str, Any]]:
    """Rewrite cached raw records with taxonomy hierarchy enrichment."""
    local_records_by_id = records_by_lpsn_id([*candidate_records, *records])
    if config.enrich_taxonomy_hierarchy:
        prefetch_taxonomy_records(client, local_records_by_id, log=log)
    hierarchy_cache = seed_hierarchy_cache(records)
    total = len(records)
    enriched_records: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        needs_hierarchy = config.enrich_taxonomy_hierarchy and "_pathogen_atlas_hierarchy" not in record
        needs_correct_name = should_resolve_correct_name(record) and "_pathogen_atlas_accepted_record" not in record
        enriched = (
            record
            if not needs_hierarchy and not needs_correct_name
            else client.enrich_record(
                record,
                enrich_taxonomy_hierarchy=config.enrich_taxonomy_hierarchy,
                local_records_by_id=local_records_by_id,
                hierarchy_cache=hierarchy_cache,
            )
        )
        enriched_records.append(enriched)
        local_records_by_id.update(records_by_lpsn_id([enriched]))
        if index == 1 or index % config.progress_log_every == 0 or index >= total:
            write_progress(
                config.progress_path,
                status="enriching_taxonomy",
                mode="enrichment_backfill",
                enrich_taxonomy_hierarchy=config.enrich_taxonomy_hierarchy,
                total_records=total,
                candidate_records=len(candidate_records),
                retrieved_records=len(candidate_records),
                completed_records=index,
                remaining_records=max(total - index, 0),
                candidate_records_path=str(config.candidate_records_path),
                raw_records_path=str(config.raw_records_path),
            )
            log_progress(
                f"Phase 1 enrichment backfill: {index}/{total} cached records enriched; "
                f"{max(total - index, 0)} remaining.",
                log,
            )
    write_jsonl(config.raw_records_path, enriched_records)
    return enriched_records


def acquire_lpsn_species_records(
    config: LpsnSourceConfig,
    force_refresh: bool = False,
    resume: bool = True,
    log: bool = True,
) -> list[dict[str, Any]]:
    """Acquire LPSN species records, supporting live progress and resume."""
    cached_backfill_only = False
    if config.raw_records_path.exists() and not force_refresh:
        progress = load_progress(config.progress_path)
        active_status = progress.get("status") in {"retrieving", "resolving_synonyms", "enriching", "running"}
        if not resume or not active_status:
            records = read_jsonl(config.raw_records_path)
            missing_hierarchy = records_missing_taxonomy_hierarchy(records)
            missing_correct_names = records_missing_correct_name_resolution(records)
            if (config.enrich_taxonomy_hierarchy and missing_hierarchy) or missing_correct_names:
                cached_backfill_only = True
                log_progress(
                    "Phase 1 acquisition: cached raw records need enrichment backfill "
                    f"(missing_taxonomy_hierarchy={missing_hierarchy if config.enrich_taxonomy_hierarchy else 0}, "
                    f"missing_correct_name_records={missing_correct_names}); using local/cache first.",
                    log,
                )
            else:
                log_progress(f"Phase 1 acquisition: using cached raw records at {config.raw_records_path}.", log)
                return records

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

    if (
        existing_records
        and (
            (config.enrich_taxonomy_hierarchy and records_missing_taxonomy_hierarchy(existing_records))
            or records_missing_correct_name_resolution(existing_records)
        )
    ):
        existing_records = backfill_taxonomy_hierarchy(
            config=config,
            client=client,
            records=existing_records,
            candidate_records=candidate_records,
            log=log,
        )
        if cached_backfill_only:
            write_progress(
                config.progress_path,
                status="completed",
                mode="enrichment_backfill",
                enrich_taxonomy_hierarchy=config.enrich_taxonomy_hierarchy,
                total_records=len(existing_records),
                candidate_records=len(candidate_records),
                retrieved_records=len(candidate_records),
                completed_records=len(existing_records),
                remaining_records=0,
                candidate_records_path=str(config.candidate_records_path),
                raw_records_path=str(config.raw_records_path),
            )
            log_progress(f"Phase 1 enrichment backfill: completed {len(existing_records)} cached records.", log)
            return existing_records
        completed_ids = {record_identity(record) for record in existing_records}

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
    if config.enrich_taxonomy_hierarchy:
        prefetch_taxonomy_records(client, local_records_by_id, log=log)
    hierarchy_cache = seed_hierarchy_cache(existing_records)
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
            hierarchy_cache=hierarchy_cache,
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
