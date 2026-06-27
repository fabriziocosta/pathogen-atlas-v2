"""Tests for Phase 1 species synonym rebuild."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

from pathogen_atlas_v2.species.acquire import (
    LpsnClientWrapper,
    LpsnCredentials,
    acquire_lpsn_species_records,
    load_species_source_config,
    write_progress,
    SpeciesAcquisitionError,
    load_lpsn_credentials,
)
from pathogen_atlas_v2.species.export_mappings import run_phase1
from pathogen_atlas_v2.species.normalise import (
    normalise_lpsn_records,
    normalize_name,
    parse_species_name,
)
from pathogen_atlas_v2.species.synonyms import SynonymLookup, build_synonym_mapping


def fixture_records() -> list[dict]:
    """Return a tiny LPSN-like fixture with accepted and synonym records."""
    accepted = {
        "id": 101,
        "full_name": "Granulicatella adiacens",
        "category": "species",
        "lpsn_taxonomic_status": "correct name",
        "risk_grp": 2,
        "_pathogen_atlas_hierarchy": [
            {"category": "genus", "full_name": "Granulicatella"},
            {"category": "family", "full_name": "Aerococcaceae"},
            {"category": "order", "full_name": "Lactobacillales"},
            {"category": "class", "full_name": "Bacilli"},
            {"category": "phylum", "full_name": "Bacillota"},
            {"category": "domain", "full_name": "Bacteria"},
        ],
    }
    synonym = {
        "id": 102,
        "full_name": "Abiotrophia adiacens",
        "category": "species",
        "lpsn_taxonomic_status": "synonym",
        "lpsn_correct_name_id": 101,
        "_pathogen_atlas_hierarchy": [
            {"category": "genus", "full_name": "Abiotrophia"},
        ],
        "_pathogen_atlas_accepted_record": accepted,
        "_pathogen_atlas_accepted_hierarchy": accepted["_pathogen_atlas_hierarchy"],
    }
    subspecies = {
        "id": 103,
        "full_name": "Escherichia coli subsp. coli",
        "category": "subspecies",
        "lpsn_taxonomic_status": "correct name",
        "_pathogen_atlas_hierarchy": [
            {"category": "genus", "full_name": "Escherichia"},
            {"category": "family", "full_name": "Enterobacteriaceae"},
            {"category": "domain", "full_name": "Bacteria"},
        ],
    }
    return [accepted, synonym, subspecies]


def write_jsonl(path: Path, records: list[dict]) -> None:
    """Write a JSONL fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record))
            handle.write("\n")


def write_phase1_config(tmp_path: Path, raw_path: Path, v1_path: Path) -> Path:
    """Write a minimal Phase 1 config that uses an existing raw cache."""
    config_path = tmp_path / "species_sources.yaml"
    config_path.write_text(
        f"""
sources:
  - name: lpsn
    category: species
    wait_time_seconds: 0
    max_retries: 1
    credentials_path: {tmp_path / "missing_credentials.yaml"}
    raw_records_path: {raw_path}
    normalised_records_path: {tmp_path / "data/01_species/interim/lpsn_species_normalised.parquet"}
    release:
      species_canonical_path: {tmp_path / "data/01_species/release/species_canonical.parquet"}
      species_synonym_mapping_path: {tmp_path / "data/01_species/release/species_synonym_mapping.parquet"}
      species_taxonomy_path: {tmp_path / "data/01_species/release/species_taxonomy.parquet"}
      metadata_path: {tmp_path / "data/01_species/release/species_mapping_metadata.json"}
    v1_reference:
      species_to_canonical_path: {v1_path}
      bacteria_synonym_lookup_path: {tmp_path / "missing_bacteria_synonym_lookup.csv"}
      pathogen_atlas_species_path: {tmp_path / "missing_pathogen_atlas_species.csv"}
      taxonomy_path: {tmp_path / "missing_taxonomy.csv"}
""",
        encoding="utf-8",
    )
    return config_path


def write_lpsn_credentials(path: Path) -> None:
    """Write local fake credentials."""
    path.write_text("lpsn:\n  username: user\n  password: pass\n", encoding="utf-8")


def test_credential_loader_valid_missing_and_malformed(tmp_path: Path) -> None:
    valid = tmp_path / "lpsn_credentials.yaml"
    valid.write_text("lpsn:\n  username: user\n  password: pass\n", encoding="utf-8")
    credentials = load_lpsn_credentials(valid)
    assert credentials.username == "user"
    assert credentials.password == "pass"

    with pytest.raises(SpeciesAcquisitionError, match="Missing LPSN credentials"):
        load_lpsn_credentials(tmp_path / "missing.yaml")

    malformed = tmp_path / "bad.yaml"
    malformed.write_text("not-a-mapping", encoding="utf-8")
    with pytest.raises(SpeciesAcquisitionError, match="top-level 'lpsn' mapping"):
        load_lpsn_credentials(malformed)


def test_name_normalisation_and_subspecies_parsing() -> None:
    assert normalize_name('  "Escherichia   coli"  ') == "Escherichia coli"
    assert parse_species_name("Escherichia coli subsp. coli") == ("Escherichia", "coli", "coli")


def test_synonym_mapping_accepted_and_synonym_records() -> None:
    normalised = normalise_lpsn_records(fixture_records(), retrieved_at="2026-01-01T00:00:00+00:00")
    mapping, diagnostics = build_synonym_mapping(normalised)
    assert diagnostics.duplicate_rows == 0
    lookup = SynonymLookup(mapping)
    assert lookup.canonical_name("Granulicatella adiacens") == "Granulicatella adiacens"
    assert lookup.canonical_name("Abiotrophia adiacens") == "Granulicatella adiacens"
    assert "Abiotrophia adiacens" in lookup.synonyms_for("Granulicatella adiacens")


def test_lpsn_search_all_retrieves_all_results_after_count() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.search_params = None

        def search(self, **kwargs):
            self.search_params = kwargs
            return 2

        def retrieve(self):
            yield {"id": 1, "full_name": "One species"}
            yield {"id": 2, "full_name": "Two species"}

    wrapper = LpsnClientWrapper.__new__(LpsnClientWrapper)
    wrapper.client = FakeClient()
    wrapper.wait_time_seconds = 0
    wrapper.max_retries = 0
    wrapper._record_cache = {}

    records = wrapper.search_all(taxon_name="", category="species")

    assert len(records) == 2
    assert wrapper.client.search_params == {"taxon_name": "", "category": "species"}


def test_lpsn_id_lookup_uses_persistent_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_path = tmp_path / "lpsn_lookup_cache.jsonl"
    calls = {"search": 0}

    class FakeClient:
        def __init__(self, username: str, password: str) -> None:
            pass

        def search(self, **kwargs):
            calls["search"] += 1
            if calls["search"] > 1:
                raise AssertionError("cache was not used")
            self.result = {"results": [kwargs["id"]], "next": None}
            return 1

        def retrieve(self):
            yield {"id": 101, "full_name": "Cached species", "category": "species"}

    monkeypatch.setitem(sys.modules, "lpsn", types.SimpleNamespace(LpsnClient=FakeClient))

    first = LpsnClientWrapper(
        LpsnCredentials(username="user", password="pass"),
        wait_time_seconds=0,
        max_retries=0,
        lookup_cache_path=cache_path,
    )
    assert first.get_entry_by_id(101)["full_name"] == "Cached species"

    second = LpsnClientWrapper(
        LpsnCredentials(username="user", password="pass"),
        wait_time_seconds=0,
        max_retries=0,
        lookup_cache_path=cache_path,
    )
    assert second.get_entry_by_id(101)["full_name"] == "Cached species"
    assert calls["search"] == 1


def test_lpsn_id_lookup_reauthenticates_after_invalid_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "lpsn_lookup_cache.jsonl"
    monkeypatch.setattr("pathogen_atlas_v2.species.acquire.time.sleep", lambda seconds: None)
    calls = {"client_init": 0, "retrieve": 0}

    class FakeClient:
        def __init__(self, username: str, password: str) -> None:
            calls["client_init"] += 1

        def search(self, **kwargs):
            self.result = {"results": [kwargs["id"]], "next": None}
            return 1

        def retrieve(self):
            calls["retrieve"] += 1
            if calls["retrieve"] == 1:
                raise RuntimeError('KeycloakPostError: 400 invalid_grant Token is not active')
            yield {"id": 101, "full_name": "Recovered species", "category": "species"}

    monkeypatch.setitem(sys.modules, "lpsn", types.SimpleNamespace(LpsnClient=FakeClient))

    wrapper = LpsnClientWrapper(
        LpsnCredentials(username="user", password="pass"),
        wait_time_seconds=0,
        max_retries=1,
        lookup_cache_path=cache_path,
    )

    assert wrapper.get_entry_by_id(101)["full_name"] == "Recovered species"
    assert calls["client_init"] == 2
    assert calls["retrieve"] == 2


def test_enrich_record_can_skip_taxonomy_hierarchy() -> None:
    wrapper = LpsnClientWrapper.__new__(LpsnClientWrapper)
    wrapper.get_entry_by_id = lambda taxon_id: {"id": taxon_id, "full_name": "Accepted species"}
    wrapper.reconstruct_hierarchy = lambda record: [{"category": "domain", "full_name": "Bacteria"}]
    synonym = {
        "id": 102,
        "full_name": "Synonym species",
        "category": "species",
        "lpsn_taxonomic_status": "synonym",
        "lpsn_correct_name_id": 101,
    }

    fast = wrapper.enrich_record(synonym, enrich_taxonomy_hierarchy=False)
    full = wrapper.enrich_record(synonym, enrich_taxonomy_hierarchy=True)

    assert "_pathogen_atlas_accepted_record" in fast
    assert "_pathogen_atlas_hierarchy" not in fast
    assert "_pathogen_atlas_accepted_hierarchy" not in fast
    assert "_pathogen_atlas_hierarchy" in full
    assert "_pathogen_atlas_accepted_hierarchy" in full


def test_enrich_record_prefers_local_candidate_records_for_accepted_name() -> None:
    wrapper = LpsnClientWrapper.__new__(LpsnClientWrapper)
    wrapper.get_entry_by_id = lambda taxon_id: (_ for _ in ()).throw(AssertionError("LPSN should not be called"))
    accepted = {"id": 101, "full_name": "Local accepted species", "category": "species"}
    synonym = {
        "id": 102,
        "full_name": "Local synonym species",
        "category": "species",
        "lpsn_taxonomic_status": "synonym",
        "lpsn_correct_name_id": 101,
    }

    enriched = wrapper.enrich_record(
        synonym,
        enrich_taxonomy_hierarchy=False,
        local_records_by_id={"101": accepted},
    )

    assert enriched["_pathogen_atlas_accepted_record"]["full_name"] == "Local accepted species"


def test_phase1_resume_uses_saved_candidate_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "data/01_species/raw/lpsn_species_records.jsonl"
    candidate_path = tmp_path / "data/01_species/raw/lpsn_species_candidates.jsonl"
    progress_path = tmp_path / "data/01_species/raw/lpsn_species_progress.json"
    credentials_path = tmp_path / "lpsn_credentials.yaml"
    v1_path = tmp_path / "v1_species_to_canonical.csv"
    write_lpsn_credentials(credentials_path)
    write_jsonl(candidate_path, [{"id": 1, "full_name": "One species", "category": "species"}])
    write_progress(
        progress_path,
        status="resolving_synonyms",
        total_records=1,
        candidate_records=1,
        retrieved_records=1,
        completed_records=0,
        remaining_records=1,
    )
    pd.DataFrame([{"Species": "One species", "CanonicalSpecies": "One species"}]).to_csv(v1_path, index=False)
    config_path = tmp_path / "species_sources.yaml"
    config_path.write_text(
        f"""
sources:
  - name: lpsn
    category: species
    wait_time_seconds: 0
    max_retries: 0
    credentials_path: {credentials_path}
    candidate_records_path: {candidate_path}
    raw_records_path: {raw_path}
    lookup_cache_path: {tmp_path / "data/01_species/raw/lpsn_lookup_cache.jsonl"}
    progress_path: {progress_path}
    progress_log_every: 1
    enrich_taxonomy_hierarchy: false
    normalised_records_path: {tmp_path / "data/01_species/interim/lpsn_species_normalised.parquet"}
    release:
      species_canonical_path: {tmp_path / "data/01_species/release/species_canonical.parquet"}
      species_synonym_mapping_path: {tmp_path / "data/01_species/release/species_synonym_mapping.parquet"}
      species_taxonomy_path: {tmp_path / "data/01_species/release/species_taxonomy.parquet"}
      metadata_path: {tmp_path / "data/01_species/release/species_mapping_metadata.json"}
    v1_reference:
      species_to_canonical_path: {v1_path}
""",
        encoding="utf-8",
    )
    calls = {"search": 0, "retrieve": 0}

    class FakeClient:
        def __init__(self, username: str, password: str) -> None:
            pass

        def search(self, **kwargs):
            calls["search"] += 1
            return 1

        def retrieve(self):
            calls["retrieve"] += 1
            raise AssertionError("retrieval should be skipped when candidates are complete")

    monkeypatch.setitem(sys.modules, "lpsn", types.SimpleNamespace(LpsnClient=FakeClient))

    config = load_species_source_config(config_path)
    records = acquire_lpsn_species_records(config, resume=True, log=True)
    captured = capsys.readouterr()

    assert len(records) == 1
    assert raw_path.exists()
    assert calls == {"search": 0, "retrieve": 0}
    assert "records resolved" in captured.out
    assert "records enriched" not in captured.out


def test_conflicting_mapping_detection() -> None:
    mapping = pd.DataFrame(
        [
            {"input_name": "Bacillus testus", "canonical_species": "Bacillus testus", "mapping_type": "accepted", "input_lpsn_record_id": 1, "canonical_lpsn_record_id": 1, "status": "correct", "source": "fixture"},
            {"input_name": "Bacillus testus", "canonical_species": "Other testus", "mapping_type": "synonym", "input_lpsn_record_id": 2, "canonical_lpsn_record_id": 3, "status": "synonym", "source": "fixture"},
        ]
    )
    with pytest.raises(ValueError, match="conflicting synonym mappings"):
        build_synonym_mapping(mapping)


def test_phase1_offline_pipeline_exports_expected_schema_and_v1_diagnostics(tmp_path: Path) -> None:
    raw_path = tmp_path / "data/01_species/raw/lpsn_species_records.jsonl"
    write_jsonl(raw_path, fixture_records())

    v1_path = tmp_path / "v1_species_to_canonical.csv"
    pd.DataFrame(
        [
            {"Species": "Abiotrophia adiacens", "CanonicalSpecies": "Granulicatella adiacens"},
            {"Species": "Bacillus pallidus", "CanonicalSpecies": "Bacillus pallidus"},
            {"Species": "Bacillus pallidus", "CanonicalSpecies": "Aeribacillus pallidus"},
            {"Species": "Duplicatus example", "CanonicalSpecies": "Duplicatus example"},
            {"Species": "Duplicatus example", "CanonicalSpecies": "Duplicatus example"},
        ]
    ).to_csv(v1_path, index=False)

    config_path = write_phase1_config(tmp_path, raw_path, v1_path)
    metadata = run_phase1(config_path=config_path)

    release_dir = tmp_path / "data/01_species/release"
    canonical = pd.read_parquet(release_dir / "species_canonical.parquet")
    mapping = pd.read_parquet(release_dir / "species_synonym_mapping.parquet")
    taxonomy = pd.read_parquet(release_dir / "species_taxonomy.parquet")

    assert list(canonical.columns) == [
        "canonical_species",
        "genus",
        "specific_epithet",
        "subspecies_epithet",
        "lpsn_record_id",
        "status",
        "risk_group",
        "source",
        "source_retrieved_at",
    ]
    assert mapping["canonical_species"].notna().all()
    assert "canonical_species" in taxonomy.columns
    assert metadata["record_counts"]["raw_records"] == 3
    assert metadata["v1_comparison"]["available"] is True
    assert metadata["v1_comparison"]["v1_duplicate_rows"] == 1
    assert metadata["v1_comparison"]["v1_conflicting_species"] == 1
