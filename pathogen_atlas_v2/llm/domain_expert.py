"""Resumable multi-question Jev treatment for abstracts that pass the pathogen gate."""

from __future__ import annotations

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
from pathogen_atlas_v2.llm.pathogen_evidence import (
    SUCCESS_STATUS,
    prepare_pathogen_evidence_inputs,
)
from pathogen_atlas_v2.llm.progress import format_eta


DEFAULT_QUESTION_SET_PATH = "configs/domain_expert_questions.yaml"
DEFAULT_PROBABILITY_PATH = "data/03_decisions/parsed/domain_expert_probabilities.parquet"
DEFAULT_RAW_RESPONSE_PATH = "data/03_decisions/raw_responses/jev/domain_expert_questions.jsonl"
PROBABILITY_COLUMNS = [
    "pmid",
    "canonical_species",
    "matched_names",
    "input_hash",
    "gate_run_id",
    "gate_p_yes",
    "provider",
    "model",
    "question_set_id",
    "question_set_version",
    "expert_run_id",
    "question_id",
    "concept",
    "category",
    "p_yes",
    "status",
    "attempts",
    "error",
    "completed_at",
    "raw_response_path",
]
PAIR_KEY_COLUMNS = [
    "pmid",
    "canonical_species",
    "input_hash",
    "gate_run_id",
    "provider",
    "model",
    "question_set_id",
    "question_set_version",
    "expert_run_id",
]


class DomainExpertQuestionError(RuntimeError):
    """Raised when the domain question set or pipeline inputs are invalid."""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def load_domain_expert_question_set(
    path: str | Path = DEFAULT_QUESTION_SET_PATH,
) -> dict[str, Any]:
    """Load the versioned V1 domain question set and validate its Jev shape."""
    config = load_yaml(resolve_path(path))
    required = {"id", "version", "questions"}
    if not isinstance(config, dict) or required - set(config):
        raise DomainExpertQuestionError(f"Question set {path} must define {sorted(required)}.")
    questions = config["questions"]
    if not isinstance(questions, dict) or not questions:
        raise DomainExpertQuestionError("The domain expert question set must contain a non-empty questions mapping.")
    for question_id, question in questions.items():
        needed = {"concept", "category", "definition", "type", "instructions", "criteria"}
        if not isinstance(question, dict) or needed - set(question):
            raise DomainExpertQuestionError(f"Question {question_id!r} is missing required fields.")
        if question["type"] != "noul":
            raise DomainExpertQuestionError(f"Question {question_id!r} must use Jev's 'noul' type.")
        if not isinstance(question["criteria"], dict) or not {"true", "false"}.issubset(question["criteria"]):
            raise DomainExpertQuestionError(f"Question {question_id!r} must define true and false criteria.")
    return dict(config)


def prepare_domain_expert_inputs(
    abstracts: pd.DataFrame,
    species_matches: pd.DataFrame,
    gate_decisions: pd.DataFrame,
    *,
    gate_run_id: str,
    gate_threshold: float,
) -> pd.DataFrame:
    """Select current species/abstract pairs with a completed gate score at threshold."""
    if not 0 <= float(gate_threshold) <= 1:
        raise ValueError("gate_threshold must be between 0 and 1.")
    gate_required = {"pmid", "canonical_species", "input_hash", "run_id", "p_yes", "status"}
    missing = gate_required - set(gate_decisions.columns)
    if missing:
        raise DomainExpertQuestionError(f"The pathogen decision ledger is missing columns: {sorted(missing)}")
    prepared = prepare_pathogen_evidence_inputs(abstracts, species_matches)
    eligible = gate_decisions.loc[
        gate_decisions["run_id"].astype(str).eq(str(gate_run_id))
        & gate_decisions["status"].astype(str).eq(SUCCESS_STATUS)
    ].copy()
    eligible["p_yes"] = pd.to_numeric(eligible["p_yes"], errors="coerce")
    eligible = eligible.loc[eligible["p_yes"].ge(float(gate_threshold))]
    eligible["pmid"] = eligible["pmid"].astype(str).str.strip()
    eligible["canonical_species"] = eligible["canonical_species"].astype(str).str.strip()
    eligible["input_hash"] = eligible["input_hash"].astype(str)
    eligible = eligible.drop_duplicates(["pmid", "canonical_species", "input_hash"], keep="last")
    eligible = eligible[["pmid", "canonical_species", "input_hash", "p_yes"]].rename(
        columns={"p_yes": "gate_p_yes"}
    )
    passing = prepared.merge(
        eligible,
        on=["pmid", "canonical_species", "input_hash"],
        how="inner",
        validate="one_to_one",
    )
    return passing.sort_values(["canonical_species", "pmid"]).reset_index(drop=True)


def _read_ledger(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=PROBABILITY_COLUMNS)
    frame = pd.read_parquet(path)
    missing = set(PROBABILITY_COLUMNS) - set(frame.columns)
    if missing:
        raise DomainExpertQuestionError(f"Domain expert ledger is missing columns: {sorted(missing)}")
    return frame[PROBABILITY_COLUMNS].copy()


def _write_ledger(path: Path, ledger: pd.DataFrame) -> None:
    ensure_parent(path)
    temporary = path.with_name(f".{path.name}.tmp")
    ledger[PROBABILITY_COLUMNS].to_parquet(temporary, index=False)
    temporary.replace(path)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str))
        handle.write("\n")


def _completed_pair_keys(ledger: pd.DataFrame, question_ids: set[str]) -> set[tuple[str, ...]]:
    completed: set[tuple[str, ...]] = set()
    if ledger.empty:
        return completed
    for key, group in ledger.groupby(PAIR_KEY_COLUMNS, dropna=False, sort=False):
        rows = group.loc[group["status"].astype(str).eq(SUCCESS_STATUS)]
        if set(rows["question_id"].astype(str)) == question_ids:
            completed.add(tuple(_clean_text(value) for value in key))
    return completed


def _pair_decision_rows(
    row: dict[str, Any],
    *,
    questions: dict[str, dict[str, Any]],
    question_set: dict[str, Any],
    client: DecisionClient,
    gate_run_id: str,
    expert_run_id: str,
    raw_response_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
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
        "gate_run_id": gate_run_id,
        "gate_p_yes": float(row["gate_p_yes"]),
        "provider": getattr(client, "provider", "openrouter_jev"),
        "model": client.model,
        "question_set_id": question_set["id"],
        "question_set_version": question_set["version"],
        "expert_run_id": expert_run_id,
        "status": "failed",
        "attempts": 0,
        "error": None,
        "completed_at": None,
        "raw_response_path": str(raw_response_path),
    }
    try:
        response = client.decide_many(state, questions=questions)
    except Exception as exc:
        base["attempts"] = int(getattr(exc, "attempts", 1))
        base["error"] = f"{type(exc).__name__}: {exc}"
        return [
            {**base, "question_id": question_id, "concept": question["concept"], "category": question["category"], "p_yes": None}
            for question_id, question in questions.items()
        ], None

    raw = {
        **{key: base[key] for key in ("pmid", "canonical_species", "input_hash", "gate_run_id", "gate_p_yes", "provider", "model", "question_set_id", "question_set_version", "expert_run_id")},
        "state": state,
        "response": response,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        probabilities = {
            question_id: extract_noul_probability(response, question_id)
            for question_id in questions
        }
    except Exception as exc:
        base["attempts"] = int(getattr(client, "last_attempts", 1))
        base["error"] = f"{type(exc).__name__}: {exc}"
        return [
            {**base, "question_id": question_id, "concept": question["concept"], "category": question["category"], "p_yes": None}
            for question_id, question in questions.items()
        ], raw

    base.update(
        status=SUCCESS_STATUS,
        attempts=int(getattr(client, "last_attempts", 1)),
        completed_at=raw["recorded_at"],
    )
    return [
        {
            **base,
            "question_id": question_id,
            "concept": question["concept"],
            "category": question["category"],
            "p_yes": probabilities[question_id],
        }
        for question_id, question in questions.items()
    ], raw


def run_domain_expert_questions(
    abstracts: pd.DataFrame,
    species_matches: pd.DataFrame,
    gate_decisions: pd.DataFrame,
    config: dict[str, Any] | None = None,
    *,
    gate_run_id: str,
    expert_run_id: str,
    gate_threshold: float,
    max_calls: int | None = None,
    client: DecisionClient | None = None,
    question_set_path: str | Path | None = None,
    decision_path: str | Path | None = None,
    raw_response_path: str | Path | None = None,
    log: bool = True,
) -> pd.DataFrame:
    """Ask all domain questions for each current passing species/abstract pair."""
    settings = dict(config or {})
    question_set = load_domain_expert_question_set(
        question_set_path or settings.get("expert_question_config_path", DEFAULT_QUESTION_SET_PATH)
    )
    questions = question_set["questions"]
    rows = prepare_domain_expert_inputs(
        abstracts,
        species_matches,
        gate_decisions,
        gate_run_id=gate_run_id,
        gate_threshold=gate_threshold,
    )
    if max_calls is None:
        max_calls = settings.get("expert_max_calls")
    if max_calls is not None and int(max_calls) < 0:
        raise ValueError("expert_max_calls must be non-negative or None.")
    max_calls = int(max_calls) if max_calls is not None else None

    model = str(settings.get("model", DEFAULT_JEV_MODEL))
    provider = str(settings.get("provider", "openrouter_jev"))
    probability_path = resolve_path(decision_path or settings.get("expert_decision_path", DEFAULT_PROBABILITY_PATH))
    raw_path = resolve_path(raw_response_path or settings.get("expert_raw_response_path", DEFAULT_RAW_RESPONSE_PATH))
    ledger = _read_ledger(probability_path)
    workers = int(settings.get("max_workers", 4))
    checkpoint_every = int(settings.get("expert_checkpoint_every", 25))
    if workers < 1 or checkpoint_every < 1:
        raise ValueError("max_workers and expert_checkpoint_every must be at least 1.")

    active_client = client
    active_model = active_client.model if active_client is not None else model
    active_provider = getattr(active_client, "provider", provider) if active_client is not None else provider

    key_context = {
        "gate_run_id": str(gate_run_id),
        "provider": active_provider,
        "model": active_model,
        "question_set_id": question_set["id"],
        "question_set_version": question_set["version"],
        "expert_run_id": str(expert_run_id),
    }
    successful_pairs = _completed_pair_keys(ledger, set(questions))
    pending_rows = []
    for row in rows.to_dict(orient="records"):
        pair_key = (
            _clean_text(row.get("pmid")),
            _clean_text(row.get("canonical_species")),
            _clean_text(row.get("input_hash")),
            *(_clean_text(key_context[key]) for key in PAIR_KEY_COLUMNS[3:]),
        )
        if pair_key not in successful_pairs:
            pending_rows.append(row)
    scheduled = pending_rows if max_calls is None else pending_rows[:max_calls]

    if log:
        print(
            f"Jev domain questions: {len(questions)} questions per abstract; "
            f"{len(rows)} gate-passing pairs; {len(pending_rows)} uncached; "
            f"{len(scheduled)} requests scheduled; model={active_model}.",
            flush=True,
        )
    if scheduled and active_client is None:
        active_client = OpenRouterJevClient(
            api_key=settings.get("api_key"),
            model=model,
            endpoint=str(settings.get("endpoint", DEFAULT_DECISIONS_URL)),
            timeout=float(settings.get("timeout_seconds", 90.0)),
            retries=int(settings.get("retries", 3)),
            backoff_seconds=float(settings.get("backoff_seconds", 1.0)),
        )
        active_model = active_client.model
        active_provider = getattr(active_client, "provider", provider)

    additions: list[dict[str, Any]] = []
    futures: dict[Future, dict[str, Any]] = {}
    if scheduled:
        started_at = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for row in scheduled:
                futures[pool.submit(
                    _pair_decision_rows,
                    row,
                    questions=questions,
                    question_set=question_set,
                    client=active_client,
                    gate_run_id=str(gate_run_id),
                    expert_run_id=str(expert_run_id),
                    raw_response_path=raw_path,
                )] = row
            running = set(futures)
            finished_pairs = 0
            while running:
                done, running = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    records, raw = future.result()
                    if raw is not None:
                        _append_jsonl(raw_path, raw)
                    additions.extend(records)
                    finished_pairs += 1
                    if finished_pairs % checkpoint_every == 0:
                        ledger = _merge_expert_records(ledger, additions)
                        _write_ledger(probability_path, ledger)
                        additions = []
                    if log and (finished_pairs == 1 or finished_pairs % checkpoint_every == 0 or finished_pairs == len(scheduled)):
                        finished_answers = finished_pairs * len(questions)
                        total_answers = len(scheduled) * len(questions)
                        print(
                            f"Jev domain questions: {finished_pairs}/{len(scheduled)} abstract requests complete; "
                            f"{finished_answers}/{total_answers} question outcomes recorded. "
                            f"{format_eta(started_at, finished_pairs, len(scheduled))}",
                            flush=True,
                        )

    if additions:
        ledger = _merge_expert_records(ledger, additions)
        _write_ledger(probability_path, ledger)
    elif not probability_path.exists():
        _write_ledger(probability_path, ledger)

    if rows.empty:
        return pd.DataFrame(columns=PROBABILITY_COLUMNS)
    eligible_pairs = rows[["pmid", "canonical_species", "input_hash"]].drop_duplicates()
    current = ledger.loc[
        ledger["expert_run_id"].astype(str).eq(str(expert_run_id))
        & ledger["gate_run_id"].astype(str).eq(str(gate_run_id))
        & ledger["question_set_id"].astype(str).eq(str(question_set["id"]))
        & ledger["question_set_version"].astype(str).eq(str(question_set["version"]))
        & ledger["model"].astype(str).eq(str(active_model))
    ].merge(
        eligible_pairs,
        on=["pmid", "canonical_species", "input_hash"],
        how="inner",
        validate="many_to_one",
    )
    return current[PROBABILITY_COLUMNS].sort_values(["canonical_species", "pmid", "question_id"]).reset_index(drop=True)


def _merge_expert_records(existing: pd.DataFrame, records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return existing
    additions = pd.DataFrame(records, columns=PROBABILITY_COLUMNS)
    keys = PAIR_KEY_COLUMNS + ["question_id"]
    if existing.empty:
        return additions.reset_index(drop=True)
    new_keys = set(map(tuple, additions[keys].astype(str).to_numpy()))
    existing_keys = existing[keys].astype(str).apply(tuple, axis=1)
    combined = pd.concat([existing.loc[~existing_keys.isin(new_keys)], additions], ignore_index=True)
    return combined.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)


def apply_domain_question_threshold(probabilities: pd.DataFrame, threshold: float = 0.75) -> pd.DataFrame:
    """Add yes/no labels derived from saved question probabilities."""
    threshold = float(threshold)
    if not 0 <= threshold <= 1:
        raise ValueError(f"Question confidence threshold must be between 0 and 1; got {threshold}.")
    result = probabilities.copy()
    values = pd.to_numeric(result.get("p_yes", pd.Series(index=result.index, dtype=float)), errors="coerce")
    completed = result.get("status", pd.Series(index=result.index, dtype=object)).astype(str).eq(SUCCESS_STATUS)
    scored = completed & values.notna()
    answer_yes = pd.Series(pd.NA, index=result.index, dtype="boolean")
    answer_yes.loc[scored] = values.loc[scored].ge(threshold)
    result["question_confidence_threshold"] = threshold
    result["answer_yes"] = answer_yes
    return result


def _phase_paths(settings: dict[str, Any]) -> tuple[Path, Path]:
    pubmed = load_yaml(resolve_path(str(settings.get("pubmed_config_path", "configs/pubmed_query_templates.yaml"))))
    outputs = pubmed.get("outputs", {})
    abstracts_path = resolve_path(str(settings.get("abstracts_path", outputs.get("release_abstracts_path", "data/02_abstracts/release/abstracts.parquet"))))
    matches_path = resolve_path(str(settings.get("species_matches_path", outputs.get("release_species_matches_path", "data/02_abstracts/release/abstract_species_matches.parquet"))))
    return abstracts_path, matches_path


def load_current_domain_expert_probabilities(config: dict[str, Any] | None = None) -> pd.DataFrame:
    """Load saved expert scores for currently gate-passing, unchanged abstract pairs."""
    settings = dict(config or {})
    abstracts_path, matches_path = _phase_paths(settings)
    gate_path = resolve_path(settings.get("decision_path", "data/03_decisions/parsed/pathogen_evidence_jev.parquet"))
    probability_path = resolve_path(settings.get("expert_decision_path", DEFAULT_PROBABILITY_PATH))
    if any(not path.exists() for path in (abstracts_path, matches_path, gate_path, probability_path)):
        return pd.DataFrame(columns=PROBABILITY_COLUMNS)
    eligible = prepare_domain_expert_inputs(
        pd.read_parquet(abstracts_path),
        pd.read_parquet(matches_path),
        pd.read_parquet(gate_path),
        gate_run_id=str(settings.get("run_id", "jev-human-pathogen-evidence-v1")),
        gate_threshold=float(settings.get("confidence_threshold", 0.75)),
    )
    if eligible.empty:
        return pd.DataFrame(columns=PROBABILITY_COLUMNS)
    keys = eligible[["pmid", "canonical_species", "input_hash"]].drop_duplicates()
    question_set = load_domain_expert_question_set(settings.get("expert_question_config_path", DEFAULT_QUESTION_SET_PATH))
    ledger = _read_ledger(probability_path)
    current = ledger.loc[
        ledger["expert_run_id"].astype(str).eq(str(settings.get("expert_run_id", "jev-domain-expert-features-v1")))
        & ledger["gate_run_id"].astype(str).eq(str(settings.get("run_id", "jev-human-pathogen-evidence-v1")))
        & ledger["question_set_id"].astype(str).eq(str(question_set["id"]))
        & ledger["question_set_version"].astype(str).eq(str(question_set["version"]))
        & ledger["model"].astype(str).eq(str(settings.get("model", DEFAULT_JEV_MODEL)))
    ].merge(keys, on=["pmid", "canonical_species", "input_hash"], how="inner", validate="many_to_one")
    return current[PROBABILITY_COLUMNS].sort_values(["canonical_species", "pmid", "question_id"]).reset_index(drop=True)


def domain_expert_preflight(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report how many pairs pass the saved gate and how many expert requests remain."""
    settings = dict(config or {})
    abstracts_path, matches_path = _phase_paths(settings)
    gate_path = resolve_path(settings.get("decision_path", "data/03_decisions/parsed/pathogen_evidence_jev.parquet"))
    missing = [str(path) for path in (abstracts_path, matches_path, gate_path) if not path.exists()]
    if missing:
        return {"status": "not_ready", "missing_inputs": missing}
    gate = pd.read_parquet(gate_path)
    eligible = prepare_domain_expert_inputs(
        pd.read_parquet(abstracts_path),
        pd.read_parquet(matches_path),
        gate,
        gate_run_id=str(settings.get("run_id", "jev-human-pathogen-evidence-v1")),
        gate_threshold=float(settings.get("confidence_threshold", 0.75)),
    )
    question_set = load_domain_expert_question_set(settings.get("expert_question_config_path", DEFAULT_QUESTION_SET_PATH))
    ledger_path = resolve_path(settings.get("expert_decision_path", DEFAULT_PROBABILITY_PATH))
    ledger = _read_ledger(ledger_path)
    successful = _completed_pair_keys(ledger, set(question_set["questions"]))
    model = str(settings.get("model", DEFAULT_JEV_MODEL))
    expert_run_id = str(settings.get("expert_run_id", "jev-domain-expert-features-v1"))
    cached = 0
    for row in eligible.to_dict(orient="records"):
        key = (
            _clean_text(row["pmid"]),
            _clean_text(row["canonical_species"]),
            _clean_text(row["input_hash"]),
            _clean_text(settings.get("run_id", "jev-human-pathogen-evidence-v1")),
            "openrouter_jev",
            model,
            _clean_text(question_set["id"]),
            _clean_text(question_set["version"]),
            expert_run_id,
        )
        cached += key in successful
    pending = len(eligible) - cached
    max_calls = settings.get("expert_max_calls", 25)
    scheduled = pending if max_calls is None else min(pending, int(max_calls))
    return {
        "status": "ready",
        "provider": "openrouter_jev",
        "model": model,
        "question_set_id": question_set["id"],
        "question_set_version": question_set["version"],
        "questions_per_abstract": len(question_set["questions"]),
        "gate_run_id": settings.get("run_id", "jev-human-pathogen-evidence-v1"),
        "gate_threshold": float(settings.get("confidence_threshold", 0.75)),
        "gate_passing_species_abstract_pairs": len(eligible),
        "already_completed_pairs": cached,
        "pending_pairs": pending,
        "max_calls": max_calls,
        "scheduled_api_calls": scheduled,
        "answers_scheduled": scheduled * len(question_set["questions"]),
        "decision_path": str(ledger_path),
    }


def run_domain_expert_phase(config: dict[str, Any] | None = None, *, log: bool = True) -> dict[str, Any]:
    """Load Phase 2 and gate outputs, then run the multi-question Jev stage."""
    settings = dict(config or {})
    abstracts_path, matches_path = _phase_paths(settings)
    gate_path = resolve_path(settings.get("decision_path", "data/03_decisions/parsed/pathogen_evidence_jev.parquet"))
    for path in (abstracts_path, matches_path, gate_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing domain-question input: {path}")
    gate_decisions = pd.read_parquet(gate_path)
    results = run_domain_expert_questions(
        pd.read_parquet(abstracts_path),
        pd.read_parquet(matches_path),
        gate_decisions,
        settings,
        gate_run_id=str(settings.get("run_id", "jev-human-pathogen-evidence-v1")),
        expert_run_id=str(settings.get("expert_run_id", "jev-domain-expert-features-v1")),
        gate_threshold=float(settings.get("confidence_threshold", 0.75)),
        max_calls=settings.get("expert_max_calls", 25),
        log=log,
    )
    counts = results["status"].value_counts(dropna=False).to_dict() if not results.empty else {}
    return {
        "status": "completed_with_failures" if counts.get("failed", 0) else "completed",
        "provider": "openrouter_jev",
        "model": str(settings.get("model", DEFAULT_JEV_MODEL)),
        "gate_threshold": float(settings.get("confidence_threshold", 0.75)),
        "question_count": int(results["question_id"].nunique()) if not results.empty else 0,
        "record_counts": {
            "question_answer_rows": len(results),
            "passing_pairs": int(results[["pmid", "canonical_species"]].drop_duplicates().shape[0]) if not results.empty else 0,
            "completed": counts.get(SUCCESS_STATUS, 0),
            "failed": counts.get("failed", 0),
        },
        "outputs": {
            "probabilities": str(resolve_path(settings.get("expert_decision_path", DEFAULT_PROBABILITY_PATH))),
            "raw_responses": str(resolve_path(settings.get("expert_raw_response_path", DEFAULT_RAW_RESPONSE_PATH))),
        },
    }


__all__ = [
    "DEFAULT_QUESTION_SET_PATH",
    "PROBABILITY_COLUMNS",
    "DomainExpertQuestionError",
    "apply_domain_question_threshold",
    "domain_expert_preflight",
    "load_domain_expert_question_set",
    "load_current_domain_expert_probabilities",
    "prepare_domain_expert_inputs",
    "run_domain_expert_phase",
    "run_domain_expert_questions",
]
