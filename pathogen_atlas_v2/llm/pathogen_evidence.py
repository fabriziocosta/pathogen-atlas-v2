"""Resumable Jev scoring for abstract-level human pathogen evidence."""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from pathogen_atlas_v2.common.io import ensure_parent, load_yaml, resolve_path
from pathogen_atlas_v2.llm.jev import (
    DEFAULT_DECISIONS_URL,
    DEFAULT_JEV_MODEL,
    DecisionClient,
    OpenRouterJevClient,
    build_pathogen_evidence_state,
    extract_noul_probability,
)
from pathogen_atlas_v2.llm.progress import format_eta


QUESTION_CONFIG_PATH = "configs/llm_questions.yaml"
QUESTION_KEY = "human_pathogen_evidence"
DEFAULT_DECISION_PATH = "data/03_decisions/parsed/pathogen_evidence_jev.parquet"
DEFAULT_RAW_RESPONSE_PATH = "data/03_decisions/raw_responses/jev/pathogen_evidence.jsonl"
DECISION_COLUMNS = [
    "pmid",
    "canonical_species",
    "matched_names",
    "input_hash",
    "provider",
    "model",
    "question_id",
    "question_version",
    "run_id",
    "p_yes",
    "status",
    "attempts",
    "error",
    "completed_at",
    "raw_response_path",
]
KEY_COLUMNS = [
    "pmid",
    "canonical_species",
    "input_hash",
    "provider",
    "model",
    "question_id",
    "question_version",
    "run_id",
]
SUCCESS_STATUS = "completed"


class PathogenEvidenceError(RuntimeError):
    """Raised when Phase 2 inputs or the versioned question are invalid."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_pathogen_evidence_question(
    path: str | Path = QUESTION_CONFIG_PATH,
    question_key: str = QUESTION_KEY,
) -> dict[str, Any]:
    """Load and validate the versioned Jev question from tracked YAML."""
    config = load_yaml(resolve_path(path))
    question = config.get("questions", {}).get(question_key)
    required = {"id", "version", "type", "instructions", "criteria"}
    if not isinstance(question, dict):
        raise PathogenEvidenceError(f"Question {question_key!r} is missing from {path}.")
    missing = required - set(question)
    if missing:
        raise PathogenEvidenceError(f"Question {question_key!r} is missing fields: {sorted(missing)}")
    if question["type"] != "noul":
        raise PathogenEvidenceError("Pathogen evidence question must use Jev's 'noul' type.")
    if not isinstance(question["criteria"], dict) or not {"true", "false"}.issubset(question["criteria"]):
        raise PathogenEvidenceError("The noul question criteria must define both 'true' and 'false'.")
    return dict(question)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values: list[Any] = [value]
    else:
        try:
            if pd.isna(value):
                return []
        except (TypeError, ValueError):
            pass
        try:
            values = list(value)
        except TypeError:
            values = [value]
    return sorted({str(name).strip() for name in values if str(name).strip()}, key=str.casefold)


def _is_selected(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().casefold() in {"true", "1", "yes", "y"}


def prepare_pathogen_evidence_inputs(
    abstracts: pd.DataFrame,
    species_matches: pd.DataFrame,
) -> pd.DataFrame:
    """Join fetched abstracts to selected species matches and deduplicate pairs."""
    abstract_required = {"pmid", "title", "abstract"}
    match_required = {"pmid", "canonical_species", "selected_for_fetch"}
    missing_abstract = abstract_required - set(abstracts.columns)
    missing_matches = match_required - set(species_matches.columns)
    if missing_abstract or missing_matches:
        raise PathogenEvidenceError(
            f"Missing input columns: abstracts={sorted(missing_abstract)}, "
            f"species_matches={sorted(missing_matches)}"
        )

    selected = species_matches.loc[species_matches["selected_for_fetch"].map(_is_selected)].copy()
    selected["pmid"] = selected["pmid"].map(_clean_text)
    selected["canonical_species"] = selected["canonical_species"].map(_clean_text)
    selected = selected[(selected["pmid"] != "") & (selected["canonical_species"] != "")]

    alias_map: dict[tuple[str, str], set[str]] = {}
    for row in selected.itertuples(index=False):
        key = (_clean_text(row.pmid), _clean_text(row.canonical_species))
        alias_map.setdefault(key, set()).update(_names(getattr(row, "matched_names", [])))

    articles = abstracts[["pmid", "title", "abstract"]].copy()
    articles["pmid"] = articles["pmid"].map(_clean_text)
    articles["title"] = articles["title"].map(_clean_text)
    articles["abstract"] = articles["abstract"].map(_clean_text)
    articles = articles.drop_duplicates(subset=["pmid"], keep="last")

    pairs = selected[["pmid", "canonical_species"]].drop_duplicates()
    joined = pairs.merge(articles, on="pmid", how="left", validate="many_to_one")
    joined["title"] = joined["title"].fillna("")
    joined["abstract"] = joined["abstract"].fillna("")
    joined["matched_names"] = [
        sorted(alias_map.get((row.pmid, row.canonical_species), set()), key=str.casefold)
        for row in joined.itertuples(index=False)
    ]
    joined["input_hash"] = [
        hash_input(
            pmid=row.pmid,
            canonical_species=row.canonical_species,
            matched_names=row.matched_names,
            title=row.title,
            abstract=row.abstract,
        )
        for row in joined.itertuples(index=False)
    ]
    return joined.sort_values(["canonical_species", "pmid"]).reset_index(drop=True)


def hash_input(
    *,
    pmid: str,
    canonical_species: str,
    matched_names: list[str],
    title: str,
    abstract: str,
) -> str:
    payload = {
        "pmid": pmid,
        "canonical_species": canonical_species,
        "matched_names": matched_names,
        "title": title,
        "abstract": abstract,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_decisions(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=DECISION_COLUMNS)
    decisions = pd.read_parquet(path)
    missing = set(DECISION_COLUMNS) - set(decisions.columns)
    if missing:
        raise PathogenEvidenceError(f"Decision ledger is missing columns: {sorted(missing)}")
    return decisions[DECISION_COLUMNS].copy()


def _write_decisions(path: Path, decisions: pd.DataFrame) -> None:
    ensure_parent(path)
    temporary = path.with_name(f".{path.name}.tmp")
    decisions[DECISION_COLUMNS].to_parquet(temporary, index=False)
    temporary.replace(path)


def apply_confidence_threshold(decisions: pd.DataFrame, threshold: float = 0.75) -> pd.DataFrame:
    """Add a nullable acceptance flag for completed decisions at the given p_yes threshold."""
    threshold = float(threshold)
    if not 0 <= threshold <= 1:
        raise ValueError(f"Confidence threshold must be between 0 and 1; got {threshold}.")
    result = decisions.copy()
    probabilities = pd.to_numeric(result.get("p_yes", pd.Series(index=result.index, dtype=float)), errors="coerce")
    completed = result.get("status", pd.Series(index=result.index, dtype=object)).eq(SUCCESS_STATUS)
    scored = completed & probabilities.notna()
    accepted = pd.Series(pd.NA, index=result.index, dtype="boolean")
    accepted.loc[scored] = probabilities.loc[scored].ge(threshold)
    result["confidence_threshold"] = threshold
    result["accepted"] = accepted
    return result


def _append_raw_response(path: Path, record: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str))
        handle.write("\n")


def _record_key(record: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_clean_text(record.get(column)) for column in KEY_COLUMNS)


def _score_one(
    row: dict[str, Any],
    *,
    client: DecisionClient,
    provider: str,
    question: dict[str, Any],
    question_id: str,
    run_id: str,
    raw_response_path: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    state = build_pathogen_evidence_state(
        canonical_species=row["canonical_species"],
        matched_names=row["matched_names"],
        title=row["title"],
        abstract=row["abstract"],
    )
    base = {
        "pmid": row["pmid"],
        "canonical_species": row["canonical_species"],
        "matched_names": row["matched_names"],
        "input_hash": row["input_hash"],
        "provider": provider,
        "model": client.model,
        "question_id": question_id,
        "question_version": question["version"],
        "run_id": run_id,
        "p_yes": None,
        "status": "failed",
        "attempts": 0,
        "error": None,
        "completed_at": None,
        "raw_response_path": str(raw_response_path),
    }
    if not row["title"] and not row["abstract"]:
        base.update(status="missing_abstract", error="No title or abstract was available for this PMID.")
        return base, None

    try:
        response = client.decide(state, question_id=question_id, question=question)
    except Exception as exc:
        base["attempts"] = int(getattr(exc, "attempts", 1))
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base, None
    raw_record = {
        "pmid": row["pmid"],
        "canonical_species": row["canonical_species"],
        "input_hash": row["input_hash"],
        "provider": provider,
        "model": client.model,
        "question_id": question_id,
        "question_version": question["version"],
        "run_id": run_id,
        "state": state,
        "response": response,
        "recorded_at": utc_now_iso(),
    }
    try:
        probability = extract_noul_probability(response, question_id)
    except Exception as exc:
        base["attempts"] = int(getattr(client, "last_attempts", 1))
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base, raw_record
    base.update(
        p_yes=probability,
        status=SUCCESS_STATUS,
        attempts=int(getattr(client, "last_attempts", 1)),
        error=None,
        completed_at=raw_record["recorded_at"],
    )
    return base, raw_record


def run_pathogen_evidence(
    abstracts: pd.DataFrame,
    species_matches: pd.DataFrame,
    config: dict[str, Any] | None = None,
    *,
    run_id: str,
    max_calls: int | None = None,
    client: DecisionClient | None = None,
    question_config_path: str | Path | None = None,
    decision_path: str | Path | None = None,
    raw_response_path: str | Path | None = None,
    log: bool = True,
) -> pd.DataFrame:
    """Score selected species/abstract rows and return records for this run.

    Prior successful records are reused when all key fields match. Failed records
    are retryable on the next invocation; thresholding remains a later operation.
    """
    settings = dict(config or {})
    if not str(run_id).strip():
        raise ValueError("run_id must be a non-empty string.")
    provider = str(settings.get("provider", "openrouter_jev"))
    if provider != "openrouter_jev" and client is None:
        raise ValueError(f"Unsupported decision provider for this runner: {provider!r}.")
    model = str(settings.get("model", DEFAULT_JEV_MODEL))
    question_key = str(settings.get("question_key", QUESTION_KEY))
    question_path = question_config_path or settings.get("question_config_path", QUESTION_CONFIG_PATH)
    question = load_pathogen_evidence_question(question_path, question_key)
    question_id = str(question["id"])
    workers = int(settings.get("max_workers", 4))
    retries = int(settings.get("retries", 3))
    backoff = float(settings.get("backoff_seconds", 1.0))
    timeout = float(settings.get("timeout_seconds", 90.0))
    checkpoint_every = int(settings.get("checkpoint_every", 25))
    if workers < 1 or checkpoint_every < 1:
        raise ValueError("max_workers and checkpoint_every must be at least 1.")
    if max_calls is None:
        max_calls = settings.get("max_calls")
    if max_calls is not None and int(max_calls) < 0:
        raise ValueError("max_calls must be non-negative or None.")
    max_calls = int(max_calls) if max_calls is not None else None

    prepared = prepare_pathogen_evidence_inputs(abstracts, species_matches)
    output_path = resolve_path(decision_path or settings.get("decision_path", DEFAULT_DECISION_PATH))
    raw_path = resolve_path(raw_response_path or settings.get("raw_response_path", DEFAULT_RAW_RESPONSE_PATH))
    ledger = _read_decisions(output_path)
    if client is None and any(
        _clean_text(row.abstract) or _clean_text(row.title)
        for row in prepared.itertuples(index=False)
    ):
        client = OpenRouterJevClient(
            api_key=settings.get("api_key"),
            model=model,
            endpoint=str(settings.get("endpoint", DEFAULT_DECISIONS_URL)),
            timeout=timeout,
            retries=retries,
            backoff_seconds=backoff,
        )
    active_client = client
    active_model = active_client.model if active_client is not None else model
    active_provider = getattr(active_client, "provider", provider) if active_client is not None else provider

    uncompleted: list[dict[str, Any]] = []
    successful_keys = {
        _record_key(record)
        for record in ledger.to_dict(orient="records")
        if _clean_text(record.get("status")) == SUCCESS_STATUS
    }
    for row in prepared.to_dict(orient="records"):
        candidate = {
            "pmid": row["pmid"],
            "canonical_species": row["canonical_species"],
            "input_hash": row["input_hash"],
            "provider": active_provider,
            "model": active_model,
            "question_id": question_id,
            "question_version": question["version"],
            "run_id": str(run_id),
        }
        if _record_key(candidate) not in successful_keys:
            uncompleted.append(row)
    missing_abstract_rows = [
        row for row in uncompleted if not row["title"] and not row["abstract"]
    ]
    pending = [
        row for row in uncompleted if row["title"] or row["abstract"]
    ]
    if max_calls is not None:
        pending = pending[:max_calls]

    if log:
        print(
            f"Jev pathogen evidence: {len(pending)} requests scheduled; "
            f"{len(missing_abstract_rows)} selected pairs lack abstracts; "
            f"run_id={run_id}; model={active_model}.",
            flush=True,
        )

    if pending and active_client is None:
        raise RuntimeError("No Jev client is available for the pending decisions.")

    new_records: list[dict[str, Any]] = [
        {
            "pmid": row["pmid"],
            "canonical_species": row["canonical_species"],
            "matched_names": row["matched_names"],
            "input_hash": row["input_hash"],
            "provider": active_provider,
            "model": active_model,
            "question_id": question_id,
            "question_version": question["version"],
            "run_id": str(run_id),
            "p_yes": None,
            "status": "missing_abstract",
            "attempts": 0,
            "error": "No title or abstract was available for this PMID.",
            "completed_at": None,
            "raw_response_path": str(raw_path),
        }
        for row in missing_abstract_rows
    ]
    futures: dict[Future, dict[str, Any]] = {}
    completed_since_checkpoint = 0
    if pending:
        started_at = time.monotonic()
        completed_requests = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for row in pending:
                futures[pool.submit(
                    _score_one,
                    row,
                    client=active_client,
                    provider=active_provider,
                    question=question,
                    question_id=question_id,
                    run_id=str(run_id),
                    raw_response_path=raw_path,
                )] = row

            running = set(futures)
            while running:
                done, running = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    row = futures[future]
                    try:
                        record, raw_record = future.result()
                    except Exception as exc:
                        record = {
                            "pmid": row["pmid"],
                            "canonical_species": row["canonical_species"],
                            "matched_names": row["matched_names"],
                            "input_hash": row["input_hash"],
                            "provider": active_provider,
                            "model": active_model,
                            "question_id": question_id,
                            "question_version": question["version"],
                            "run_id": str(run_id),
                            "p_yes": None,
                            "status": "failed",
                            "attempts": int(getattr(exc, "attempts", 1)),
                            "error": f"{type(exc).__name__}: {exc}",
                            "completed_at": None,
                            "raw_response_path": str(raw_path),
                        }
                        raw_record = None
                    if raw_record is not None:
                        _append_raw_response(raw_path, raw_record)
                    new_records.append(record)
                    completed_requests += 1
                    completed_since_checkpoint += 1
                    if completed_since_checkpoint >= checkpoint_every:
                        ledger = _merge_records(ledger, new_records)
                        _write_decisions(output_path, ledger)
                        new_records = []
                        completed_since_checkpoint = 0
                    if log and (
                        completed_requests == 1
                        or completed_requests % checkpoint_every == 0
                        or completed_requests == len(pending)
                    ):
                        print(
                            f"Jev pathogen evidence: {completed_requests}/{len(pending)} requests complete; "
                            f"{format_eta(started_at, completed_requests, len(pending))}",
                            flush=True,
                        )

    if new_records:
        ledger = _merge_records(ledger, new_records)
    if pending or not output_path.exists():
        _write_decisions(output_path, ledger)

    current = ledger.loc[ledger["run_id"].astype(str).eq(str(run_id))].copy()
    if current.empty:
        return pd.DataFrame(columns=DECISION_COLUMNS)
    current = current.drop_duplicates(subset=KEY_COLUMNS, keep="last")
    return current[DECISION_COLUMNS].sort_values(["canonical_species", "pmid"]).reset_index(drop=True)


def _merge_records(existing: pd.DataFrame, new_records: list[dict[str, Any]]) -> pd.DataFrame:
    if not new_records:
        return existing
    additions = pd.DataFrame(new_records, columns=DECISION_COLUMNS)
    if existing.empty:
        return additions.reset_index(drop=True)
    new_keys = {_record_key(record) for record in new_records}
    existing_keys = existing[KEY_COLUMNS].astype(str).apply(tuple, axis=1)
    existing = existing.loc[~existing_keys.isin(new_keys)].copy()
    if existing.empty:
        return additions.reset_index(drop=True)
    combined = pd.concat([existing, additions], ignore_index=True)
    return combined.drop_duplicates(subset=KEY_COLUMNS, keep="last").reset_index(drop=True)


def run_phase3(config: dict[str, Any] | None = None, *, log: bool = True) -> dict[str, Any]:
    """Load Phase 2 release tables and execute configured Jev scoring."""
    settings = dict(config or {})
    pubmed_config = load_yaml(resolve_path(str(settings.get("pubmed_config_path", "configs/pubmed_query_templates.yaml"))))
    outputs = pubmed_config.get("outputs", {})
    abstracts_path = resolve_path(str(settings.get("abstracts_path", outputs.get("release_abstracts_path", "data/02_abstracts/release/abstracts.parquet"))))
    matches_path = resolve_path(str(settings.get("species_matches_path", outputs.get("release_species_matches_path", "data/02_abstracts/release/abstract_species_matches.parquet"))))
    for path in (abstracts_path, matches_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing Phase 2 input: {path}. Run Phase 2 abstract acquisition first.")
    abstracts = pd.read_parquet(abstracts_path)
    species_matches = pd.read_parquet(matches_path)
    run_id = str(settings.get("run_id", "jev-human-pathogen-evidence-v1"))
    decisions = run_pathogen_evidence(
        abstracts,
        species_matches,
        settings,
        run_id=run_id,
        max_calls=settings.get("max_calls"),
        log=log,
    )
    status_counts = decisions["status"].value_counts(dropna=False).to_dict() if not decisions.empty else {}
    return {
        "status": "completed_with_failures" if status_counts.get("failed", 0) else "completed",
        "source": "openrouter_jev",
        "model": str(settings.get("model", DEFAULT_JEV_MODEL)),
        "run_id": run_id,
        "record_counts": {
            "decisions": len(decisions),
            "completed": status_counts.get("completed", 0),
            "failed": status_counts.get("failed", 0),
            "missing_abstract": status_counts.get("missing_abstract", 0),
        },
        "outputs": {
            "decisions": str(resolve_path(settings.get("decision_path", DEFAULT_DECISION_PATH))),
            "raw_responses": str(resolve_path(settings.get("raw_response_path", DEFAULT_RAW_RESPONSE_PATH))),
        },
    }


def phase3_preflight(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize selected input coverage and remaining work without API calls."""
    settings = dict(config or {})
    pubmed_config = load_yaml(resolve_path(str(settings.get("pubmed_config_path", "configs/pubmed_query_templates.yaml"))))
    outputs = pubmed_config.get("outputs", {})
    abstracts_path = resolve_path(str(settings.get("abstracts_path", outputs.get("release_abstracts_path", "data/02_abstracts/release/abstracts.parquet"))))
    matches_path = resolve_path(str(settings.get("species_matches_path", outputs.get("release_species_matches_path", "data/02_abstracts/release/abstract_species_matches.parquet"))))
    missing = [str(path) for path in (abstracts_path, matches_path) if not path.exists()]
    if missing:
        return {"status": "not_ready", "missing_inputs": missing}
    prepared = prepare_pathogen_evidence_inputs(
        pd.read_parquet(abstracts_path),
        pd.read_parquet(matches_path),
    )
    question = load_pathogen_evidence_question(
        settings.get("question_config_path", QUESTION_CONFIG_PATH),
        str(settings.get("question_key", QUESTION_KEY)),
    )
    model = str(settings.get("model", DEFAULT_JEV_MODEL))
    run_id = str(settings.get("run_id", "jev-human-pathogen-evidence-v1"))
    ledger_path = resolve_path(settings.get("decision_path", DEFAULT_DECISION_PATH))
    ledger = _read_decisions(ledger_path)
    successful_keys = {
        _record_key(record)
        for record in ledger.to_dict(orient="records")
        if _clean_text(record.get("status")) == SUCCESS_STATUS
    }
    pending_rows: list[dict[str, Any]] = []
    for row in prepared.to_dict(orient="records"):
        candidate = {
            "pmid": row["pmid"],
            "canonical_species": row["canonical_species"],
            "input_hash": row["input_hash"],
            "provider": "openrouter_jev",
            "model": model,
            "question_id": question["id"],
            "question_version": question["version"],
            "run_id": run_id,
        }
        if _record_key(candidate) not in successful_keys:
            pending_rows.append(row)
    pending_count = len(pending_rows)
    max_calls = settings.get("max_calls")
    request_rows = [row for row in pending_rows if row["title"] or row["abstract"]]
    scheduled_rows = request_rows if max_calls is None else request_rows[: int(max_calls)]
    missing_abstract_count = int(((prepared["title"] == "") & (prepared["abstract"] == "")).sum())
    return {
        "status": "ready",
        "provider": "openrouter_jev",
        "model": model,
        "question_id": question["id"],
        "question_version": question["version"],
        "run_id": run_id,
        "selected_species_pmid_pairs": len(prepared),
        "species_count": int(prepared["canonical_species"].nunique()),
        "missing_abstract_pairs": missing_abstract_count,
        "already_completed_for_run": len(prepared) - pending_count,
        "pending_pairs": pending_count,
        "max_calls": max_calls,
        "scheduled_api_calls": len(scheduled_rows),
        "decision_path": str(ledger_path),
        "raw_response_path": str(resolve_path(settings.get("raw_response_path", DEFAULT_RAW_RESPONSE_PATH))),
    }


__all__ = [
    "DECISION_COLUMNS",
    "PathogenEvidenceError",
    "apply_confidence_threshold",
    "load_pathogen_evidence_question",
    "phase3_preflight",
    "prepare_pathogen_evidence_inputs",
    "run_pathogen_evidence",
    "run_phase3",
]
