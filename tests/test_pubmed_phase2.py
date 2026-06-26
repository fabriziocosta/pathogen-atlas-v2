"""Tests for Phase 2 PubMed abstract acquisition."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pathogen_atlas_v2.abstracts.abstract_exports import write_phase2_outputs
from pathogen_atlas_v2.abstracts.abstract_exports import (
    build_canonical_pmid_index,
    run_pubmed_fetches,
    run_pubmed_searches,
    sample_canonical_pmids,
)
from pathogen_atlas_v2.abstracts.article_records import build_articles_dataframe, parse_pubmed_article_record
from pathogen_atlas_v2.abstracts.filter_matches import build_filter_match_rows
from pathogen_atlas_v2.abstracts.pubmed_client import (
    NcbiEntrezConfig,
    PubMedEntrezClient,
    PubMedClientError,
    load_ncbi_entrez_config,
)
from pathogen_atlas_v2.abstracts.pubmed_query_plan import (
    build_pubmed_query_plan,
    materialize_pubmed_query_plan,
)
from pathogen_atlas_v2.abstracts.pubmed_query_templates import render_pubmed_query, stable_query_hash
from pathogen_atlas_v2.abstracts.species_matches import build_species_match_rows


def write_phase1_mapping(path: Path) -> None:
    """Write a tiny Phase 1 synonym mapping fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "input_name": "Abiotrophia adiacens",
                "canonical_species": "Granulicatella adiacens",
                "mapping_type": "synonym",
                "input_lpsn_record_id": 1,
                "canonical_lpsn_record_id": 2,
                "status": "synonym",
                "source": "fixture",
            },
            {
                "input_name": "Granulicatella adiacens",
                "canonical_species": "Granulicatella adiacens",
                "mapping_type": "accepted",
                "input_lpsn_record_id": 2,
                "canonical_lpsn_record_id": 2,
                "status": "correct name",
                "source": "fixture",
            },
        ]
    ).to_parquet(path, index=False)


def write_filter_config(path: Path) -> None:
    """Write tiny filter config."""
    path.write_text(
        """
term_sets:
  human_context:
    - patient
    - human
  infection_context:
    - infection
    - abscess
""",
        encoding="utf-8",
    )


def write_template_config(tmp_path: Path, mapping_path: Path) -> Path:
    """Write tiny PubMed query-template config."""
    config_path = tmp_path / "pubmed_query_templates.yaml"
    config_path.write_text(
        f"""
source:
  name: pubmed
  min_year: 2000
  max_year: 2026
  fields:
    - Title/Abstract
  species_synonym_mapping_path: {mapping_path}
  ncbi_config_path: {tmp_path / "ncbi_entrez.yaml"}
  rate_limit_without_api_key_per_second: 3
  rate_limit_with_api_key_per_second: 10
  request_delay_seconds: 0
  max_retries: 1
  retmax: 100
  fetch_batch_size: 2
  max_pmids_per_canonical_species: 2
  oldest_pmids_per_canonical_species: 1
  pmid_sample_random_seed: 42
query_plan:
  path: {tmp_path / "data/02_abstracts/query_plan/pubmed_query_plan.yaml"}
  approval:
    status: draft
    reviewed_by: ""
    reviewed_at: ""
    notes: soft approval
templates:
  - id: exact_species_human_infection_context
    description: fixture
    template: '({{matched_names}}) AND ({{human_terms}}) AND ({{infection_terms}}) AND ("{{min_year}}"[Date - Publication] : "{{max_year}}"[Date - Publication])'
outputs:
  raw_search_results_path: {tmp_path / "data/02_abstracts/raw/pubmed_search_results.jsonl"}
  canonical_pmid_index_path: {tmp_path / "data/02_abstracts/filtered/canonical_species_pmids.parquet"}
  sampled_canonical_pmid_index_path: {tmp_path / "data/02_abstracts/filtered/canonical_species_pmids_sampled.parquet"}
  raw_article_records_path: {tmp_path / "data/02_abstracts/raw/pubmed_article_records.jsonl"}
  filtered_species_matches_path: {tmp_path / "data/02_abstracts/filtered/abstract_species_matches.parquet"}
  filtered_filter_matches_path: {tmp_path / "data/02_abstracts/filtered/abstract_filter_matches.parquet"}
  release_abstracts_path: {tmp_path / "data/02_abstracts/release/abstracts.parquet"}
  release_species_matches_path: {tmp_path / "data/02_abstracts/release/abstract_species_matches.parquet"}
  release_filter_matches_path: {tmp_path / "data/02_abstracts/release/abstract_filter_matches.parquet"}
  metadata_path: {tmp_path / "data/02_abstracts/release/abstract_acquisition_metadata.json"}
""",
        encoding="utf-8",
    )
    return config_path


def article_fixture() -> dict:
    """Return a MEDLINE-like article fixture."""
    return {
        "PMID": "123",
        "TI": "Human infection due to Abiotrophia adiacens",
        "AB": "A patient had an abscess.",
        "DP": "2024 Jan",
        "AU": ["Smith J", "Jones A"],
        "JT": "Journal of Fixtures",
        "AID": ["10.1000/example [doi]"],
        "MH": ["Humans", "Infections"],
        "OT": ["patient"],
    }


def test_render_pubmed_query_with_placeholders() -> None:
    query = render_pubmed_query(
        template='({matched_names}) AND ({human_terms}) AND ({infection_terms})',
        matched_names=["Abiotrophia adiacens", "Granulicatella adiacens"],
        field="Title/Abstract",
        human_terms=["patient"],
        infection_terms=["infection"],
        min_year=2000,
        max_year=2026,
    )
    assert '"Abiotrophia adiacens"[Title/Abstract]' in query
    assert '"Granulicatella adiacens"[Title/Abstract]' in query
    assert '"Abiotrophia adiacens"[Title/Abstract] OR "Granulicatella adiacens"[Title/Abstract]' in query
    assert '"patient"[Title/Abstract]' in query
    assert stable_query_hash(query) == stable_query_hash(query)


def test_query_plan_generation_and_yaml_schema(tmp_path: Path) -> None:
    mapping_path = tmp_path / "phase1/species_synonym_mapping.parquet"
    write_phase1_mapping(mapping_path)
    filter_path = tmp_path / "abstract_filters.yaml"
    write_filter_config(filter_path)
    template_path = write_template_config(tmp_path, mapping_path)

    plan = build_pubmed_query_plan(template_path, filter_path)
    assert len(plan["queries"]) == 1
    assert plan["approval"]["status"] == "draft"
    assert plan["queries"][0]["query_id"].startswith("q_")
    assert plan["queries"][0]["canonical_species"] == "Granulicatella adiacens"
    assert plan["queries"][0]["synonym_count"] == 2

    result = materialize_pubmed_query_plan(template_path, filter_path)
    assert Path(result["path"]).exists()
    assert result["query_count"] == 1


def test_ncbi_config_loader_validates_email(tmp_path: Path) -> None:
    config = tmp_path / "ncbi_entrez.yaml"
    config.write_text("ncbi:\n  email: test@example.org\n  api_key: ''\n  tool: pathogen-atlas-v2\n", encoding="utf-8")
    loaded = load_ncbi_entrez_config(config)
    assert loaded.email == "test@example.org"
    assert loaded.api_key is None

    missing_email = tmp_path / "bad_ncbi.yaml"
    missing_email.write_text("ncbi:\n  tool: pathogen-atlas-v2\n", encoding="utf-8")
    with pytest.raises(PubMedClientError, match="ncbi.email"):
        load_ncbi_entrez_config(missing_email)


def test_pubmed_client_mocked_search_and_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeHandle:
        def close(self) -> None:
            pass

    def fake_read(handle):
        return {"IdList": ["123", "456"]}

    def fake_parse(handle):
        return iter([article_fixture()])

    monkeypatch.setattr("pathogen_atlas_v2.abstracts.pubmed_client.Entrez.esearch", lambda **kwargs: FakeHandle())
    monkeypatch.setattr("pathogen_atlas_v2.abstracts.pubmed_client.Entrez.efetch", lambda **kwargs: FakeHandle())
    monkeypatch.setattr("pathogen_atlas_v2.abstracts.pubmed_client.Entrez.read", fake_read)
    monkeypatch.setattr("pathogen_atlas_v2.abstracts.pubmed_client.Medline.parse", fake_parse)

    client = PubMedEntrezClient(
        NcbiEntrezConfig(email="test@example.org", api_key=None, tool="fixture"),
        request_delay_seconds=0,
        max_retries=1,
        retmax=100,
    )
    assert client.search_pmids("fixture") == ["123", "456"]
    assert client.fetch_medline_records(["123"])[0]["PMID"] == "123"


def test_pubmed_searches_skip_existing_query_results() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def search_pmids(self, query: str) -> list[str]:
            self.calls.append(query)
            return ["456"]

    query_plan = {
        "queries": [
            {"query_id": "q_1", "query_hash": "1", "query": "already done"},
            {"query_id": "q_2", "query_hash": "2", "query": "pending"},
        ]
    }
    client = FakeClient()
    results = run_pubmed_searches(
        query_plan,
        client,
        existing_results=[{"query_id": "q_1", "query_hash": "1", "pmids": ["123"]}],
        log=False,
    )
    assert client.calls == ["pending"]
    assert {result["query_id"] for result in results} == {"q_1", "q_2"}


def test_pubmed_fetches_skip_existing_pmids() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def fetch_medline_records(self, pmids: list[str]) -> list[dict]:
            self.calls.append(pmids)
            return [{"PMID": pmid, "TI": f"title {pmid}"} for pmid in pmids]

    search_results = [{"query_id": "q_1", "sampled_pmids": ["123", "456", "789"]}]
    client = FakeClient()
    records = run_pubmed_fetches(
        search_results,
        client,
        existing_records=[{"PMID": "123", "TI": "already fetched"}],
        batch_size=2,
        log=False,
    )
    assert client.calls == [["456", "789"]]
    assert {record["PMID"] for record in records} == {"123", "456", "789"}


def test_article_parser_species_matches_and_filter_matches() -> None:
    article = parse_pubmed_article_record(article_fixture())
    assert article["pmid"] == "123"
    assert article["publication_year"] == "2024"
    assert article["doi"] == "10.1000/example"

    articles_df = build_articles_dataframe([article_fixture(), article_fixture()])
    assert len(articles_df) == 1

    filter_matches = build_filter_match_rows(articles_df, filter_config_path="configs/abstract_filters.yaml")
    assert {"human_context", "infection_context"}.issubset(set(filter_matches["filter_family"]))

    query_plan = {
        "queries": [
            {
                "query_id": "q_1",
                "query_hash": "1",
                "template_id": "template",
                "canonical_species": "Granulicatella adiacens",
                "matched_names": ["Abiotrophia adiacens", "Granulicatella adiacens"],
                "mapping_types": ["accepted", "synonym"],
                "synonym_count": 2,
                "field": "Title/Abstract",
                "query": "fixture",
            }
        ]
    }
    species_matches = build_species_match_rows(
        [{"query_id": "q_1", "query_hash": "1", "pmids": ["123", "123"]}],
        query_plan,
    )
    assert len(species_matches) == 1
    assert species_matches.loc[0, "canonical_species"] == "Granulicatella adiacens"
    assert species_matches.loc[0, "synonym_count"] == 2


def test_canonical_pmid_index_and_sampling() -> None:
    query_plan = {
        "queries": [
            {
                "query_id": "q_1",
                "canonical_species": "Granulicatella adiacens",
                "matched_names": ["Abiotrophia adiacens", "Granulicatella adiacens"],
            },
            {
                "query_id": "q_2",
                "canonical_species": "Granulicatella adiacens",
                "matched_names": ["Abiotrophia adiacens", "Granulicatella adiacens"],
            },
        ]
    }
    index = build_canonical_pmid_index(
        [
            {"query_id": "q_1", "pmids": ["1", "2", "3"]},
            {"query_id": "q_2", "pmids": ["3", "4"]},
        ],
        query_plan,
    )
    assert index.loc[0, "n_pmids"] == 4
    sampled = sample_canonical_pmids(
        index,
        max_pmids_per_canonical_species=3,
        oldest_pmids_per_canonical_species=2,
        random_seed=42,
    )
    assert sampled.loc[0, "n_pmids"] == 3
    assert sampled.loc[0, "n_pmids_available"] == 4
    assert sampled.loc[0, "oldest_pmids"] == ["1", "2"]
    assert not (set(sampled.loc[0, "oldest_pmids"]) & set(sampled.loc[0, "sampled_remaining_pmids"]))


def test_phase2_release_exports_without_secrets(tmp_path: Path) -> None:
    mapping_path = tmp_path / "phase1/species_synonym_mapping.parquet"
    write_phase1_mapping(mapping_path)
    filter_path = tmp_path / "abstract_filters.yaml"
    write_filter_config(filter_path)
    template_path = write_template_config(tmp_path, mapping_path)
    plan = build_pubmed_query_plan(template_path, filter_path)
    search_results = [{"query_id": plan["queries"][0]["query_id"], "query_hash": plan["queries"][0]["query_hash"], "pmids": ["123"]}]
    metadata = write_phase2_outputs(plan, search_results, [article_fixture()], template_path, filter_path)

    release = tmp_path / "data/02_abstracts/release"
    filtered = tmp_path / "data/02_abstracts/filtered"
    assert (release / "abstracts.parquet").exists()
    assert (release / "abstract_species_matches.parquet").exists()
    assert (release / "abstract_filter_matches.parquet").exists()
    assert (filtered / "canonical_species_pmids.parquet").exists()
    assert (filtered / "canonical_species_pmids_sampled.parquet").exists()
    assert metadata["record_counts"]["release_abstracts"] == 1
    assert metadata["sampling"]["max_pmids_per_canonical_species"] == 2
    assert metadata["sampling"]["oldest_pmids_per_canonical_species"] == 1
    assert metadata["ncbi"]["api_key_configured"] is None
    assert "test@example.org" not in str(metadata)
