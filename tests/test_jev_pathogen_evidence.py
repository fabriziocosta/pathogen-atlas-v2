"""Offline tests for Jev-backed pathogen evidence decisions."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import requests

from pathogen_atlas_v2.llm.jev import (
    DEFAULT_DECISIONS_URL,
    DEFAULT_JEV_MODEL,
    JevRequestError,
    OpenRouterJevClient,
    build_pathogen_evidence_state,
    extract_noul_probability,
)
from pathogen_atlas_v2.llm.pathogen_evidence import (
    DECISION_COLUMNS,
    prepare_pathogen_evidence_inputs,
    run_pathogen_evidence,
)
from pathogen_atlas_v2.pipelines import phase3_abstract_decisions


QUESTION_ID = "human_pathogen_evidence"


class FakeDecisionClient:
    model = DEFAULT_JEV_MODEL

    def __init__(self, response=None, error=None):
        self.response = response or {"answers": {QUESTION_ID: {"type": "noul", "noul": 0.87}}}
        self.error = error
        self.calls = []

    def decide(self, state, *, question_id, question):
        self.calls.append((state, question_id, question))
        if self.error:
            raise self.error
        return self.response


class FakeHTTPResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def input_tables(abstract="A human infection was caused by Example bacterium."):
    abstracts = pd.DataFrame(
        [
            {"pmid": "101", "title": "Example infection", "abstract": abstract},
            {"pmid": "102", "title": "Carriage study", "abstract": "Colonisation only."},
        ]
    )
    species_matches = pd.DataFrame(
        [
            {
                "pmid": "101",
                "canonical_species": "Example bacterium",
                "matched_names": ["Example bacterium", "Old example name"],
                "selected_for_fetch": True,
                "query_id": "q1",
            },
            {
                "pmid": "101",
                "canonical_species": "Example bacterium",
                "matched_names": ["Example bacterium"],
                "selected_for_fetch": True,
                "query_id": "q2",
            },
            {
                "pmid": "102",
                "canonical_species": "Example bacterium",
                "matched_names": ["Example bacterium"],
                "selected_for_fetch": False,
                "query_id": "q3",
            },
        ]
    )
    return abstracts, species_matches


def run_config(tmp_path: Path) -> dict:
    return {
        "decision_path": str(tmp_path / "decisions.parquet"),
        "raw_response_path": str(tmp_path / "raw.jsonl"),
        "max_workers": 1,
        "checkpoint_every": 1,
    }


def test_state_builder_and_question_payload_shapes() -> None:
    state = build_pathogen_evidence_state(
        canonical_species="Example bacterium",
        matched_names=["Example bacterium", "Old example name"],
        title="Example infection",
        abstract="The species caused an infection.",
    )
    assert state["species"]["canonical_name"] == "Example bacterium"
    assert state["species"]["names_in_query"] == ["Example bacterium", "Old example name"]
    assert state["article"]["title"] == "Example infection"

    session = FakeSession([FakeHTTPResponse({"answers": {QUESTION_ID: {"type": "noul", "noul": 0.8}}})])
    client = OpenRouterJevClient(api_key="test-key", session=session)
    question = {
        "type": "noul",
        "instructions": "Decide whether the statement is supported.",
        "criteria": {"true": "Supported.", "false": "Not supported."},
    }
    client.decide(state, question_id=QUESTION_ID, question=question)
    url, call = session.calls[0]
    assert url == DEFAULT_DECISIONS_URL
    assert call["json"]["model"] == DEFAULT_JEV_MODEL
    assert call["json"]["state"] == state
    assert call["json"]["questions"][QUESTION_ID] == question
    assert call["headers"]["Authorization"] == "Bearer test-key"


@pytest.mark.parametrize("value", [0, 0.37, 1])
def test_extract_noul_probability_accepts_valid_values(value) -> None:
    response = {"answers": {QUESTION_ID: {"type": "noul", "noul": value}}}
    assert extract_noul_probability(response, QUESTION_ID) == float(value)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"answers": {QUESTION_ID: {"type": "choice", "noul": 0.5}}},
        {"answers": {QUESTION_ID: {"type": "noul", "noul": True}}},
        {"answers": {QUESTION_ID: {"type": "noul", "noul": 1.1}}},
    ],
)
def test_extract_noul_probability_rejects_malformed_answers(response) -> None:
    with pytest.raises(ValueError):
        extract_noul_probability(response, QUESTION_ID)


def test_openrouter_client_retries_transient_http_errors() -> None:
    session = FakeSession(
        [
            FakeHTTPResponse({}, status_code=429),
            FakeHTTPResponse({"answers": {QUESTION_ID: {"type": "noul", "noul": 0.6}}}),
        ]
    )
    client = OpenRouterJevClient(api_key="test-key", retries=1, backoff_seconds=0, session=session)
    result = client.decide(
        {"article": {"title": "t", "abstract": "a"}},
        question_id=QUESTION_ID,
        question={"type": "noul", "instructions": "q", "criteria": {}},
    )
    assert len(session.calls) == 2
    assert result["answers"][QUESTION_ID]["noul"] == 0.6
    assert client.last_attempts == 2


def test_openrouter_client_stops_after_retry_budget() -> None:
    session = FakeSession([FakeHTTPResponse({}, status_code=503), FakeHTTPResponse({}, status_code=503)])
    client = OpenRouterJevClient(api_key="test-key", retries=1, backoff_seconds=0, session=session)
    with pytest.raises(JevRequestError) as exc_info:
        client.decide({}, question_id=QUESTION_ID, question={"type": "noul", "instructions": "q"})
    assert exc_info.value.attempts == 2
    assert len(session.calls) == 2


def test_input_preparation_filters_and_deduplicates_species_pmid_pairs() -> None:
    abstracts, species_matches = input_tables()
    prepared = prepare_pathogen_evidence_inputs(abstracts, species_matches)
    assert prepared[["pmid", "canonical_species"]].to_dict("records") == [
        {"pmid": "101", "canonical_species": "Example bacterium"}
    ]
    assert prepared.iloc[0]["matched_names"] == ["Example bacterium", "Old example name"]


def test_runner_saves_and_resumes_successful_decisions(tmp_path: Path) -> None:
    abstracts, species_matches = input_tables()
    first_client = FakeDecisionClient()
    first = run_pathogen_evidence(
        abstracts,
        species_matches,
        run_config(tmp_path),
        run_id="pilot-a",
        client=first_client,
        log=False,
    )
    assert len(first_client.calls) == 1
    assert first.iloc[0]["p_yes"] == 0.87
    assert set(first.columns) == set(DECISION_COLUMNS)
    assert first.iloc[0]["status"] == "completed"
    assert Path(run_config(tmp_path)["decision_path"]).exists()
    raw = [json.loads(line) for line in Path(run_config(tmp_path)["raw_response_path"]).read_text().splitlines()]
    assert raw[0]["response"]["answers"][QUESTION_ID]["noul"] == 0.87

    second_client = FakeDecisionClient()
    second = run_pathogen_evidence(
        abstracts,
        species_matches,
        run_config(tmp_path),
        run_id="pilot-a",
        client=second_client,
        log=False,
    )
    assert second_client.calls == []
    assert len(second) == 1


def test_runner_rescores_changed_input_and_question_version(tmp_path: Path) -> None:
    abstracts, species_matches = input_tables()
    config = run_config(tmp_path)
    first_client = FakeDecisionClient()
    run_pathogen_evidence(abstracts, species_matches, config, run_id="pilot-a", client=first_client, log=False)

    changed_abstracts = abstracts.copy()
    changed_abstracts.loc[changed_abstracts["pmid"] == "101", "abstract"] = "A different abstract."
    changed_input_client = FakeDecisionClient()
    run_pathogen_evidence(changed_abstracts, species_matches, config, run_id="pilot-a", client=changed_input_client, log=False)
    assert len(changed_input_client.calls) == 1

    question_config = tmp_path / "questions.yaml"
    question_config.write_text(
        "questions:\n  human_pathogen_evidence:\n"
        "    id: human_pathogen_evidence\n"
        "    version: human-pathogen-disease-attribution-v2\n"
        "    type: noul\n"
        "    instructions: Is the evidence present?\n"
        '    criteria:\n      "true": yes\n      "false": no\n',
        encoding="utf-8",
    )
    changed_question_client = FakeDecisionClient()
    run_pathogen_evidence(
        abstracts,
        species_matches,
        config,
        run_id="pilot-a",
        client=changed_question_client,
        question_config_path=question_config,
        log=False,
    )
    assert len(changed_question_client.calls) == 1


def test_runner_retries_failed_or_malformed_decisions_on_next_run(tmp_path: Path) -> None:
    abstracts, species_matches = input_tables()
    config = run_config(tmp_path)
    bad_client = FakeDecisionClient(response={"answers": {}})
    failed = run_pathogen_evidence(abstracts, species_matches, config, run_id="pilot-a", client=bad_client, log=False)
    assert failed.iloc[0]["status"] == "failed"
    assert failed.iloc[0]["p_yes"] is None
    raw_lines = Path(config["raw_response_path"]).read_text().splitlines()
    assert len(raw_lines) == 1

    retry_client = FakeDecisionClient()
    retried = run_pathogen_evidence(abstracts, species_matches, config, run_id="pilot-a", client=retry_client, log=False)
    assert len(retry_client.calls) == 1
    assert retried.iloc[0]["status"] == "completed"


def test_missing_abstract_is_recorded_without_api_call(tmp_path: Path) -> None:
    abstracts, species_matches = input_tables()
    abstracts = abstracts.loc[abstracts["pmid"] != "101"]
    species_matches = species_matches.loc[species_matches["pmid"] == "101"]
    client = FakeDecisionClient()
    decisions = run_pathogen_evidence(
        abstracts,
        species_matches,
        run_config(tmp_path),
        run_id="pilot-a",
        client=client,
        log=False,
    )
    assert client.calls == []
    assert decisions.iloc[0]["status"] == "missing_abstract"
    assert decisions.iloc[0]["attempts"] == 0


def test_phase3_cli_honors_disabled_run_flag(monkeypatch) -> None:
    monkeypatch.setattr(phase3_abstract_decisions, "notebook_phase_parameters", lambda _: {"run_phase": False})
    monkeypatch.setattr(phase3_abstract_decisions, "phase3_preflight", lambda _: {"status": "ready"})
    monkeypatch.setattr(
        phase3_abstract_decisions,
        "run_phase3",
        lambda *_args, **_kwargs: pytest.fail("Jev runner must not run while run_phase is false."),
    )
    result = phase3_abstract_decisions.main()
    assert result["status"] == "not_run"
    assert result["preflight"]["status"] == "ready"
