"""Notebook structure checks."""

from __future__ import annotations

from pathlib import Path

import nbformat
import pandas as pd

from pathogen_atlas_v2 import notebook_utils as nbutils


EXPECTED_NOTEBOOKS = [
    "00_configure_pipeline.ipynb",
    "01_species_synonyms.ipynb",
    "02_1_pubmed_query_plan.ipynb",
    "02_2_pubmed_acquire_pmids.ipynb",
    "02_3_pubmed_acquire_abstracts.ipynb",
    "02_4_pubmed_table_stats.ipynb",
    "03_abstract_decisions.ipynb",
    "04_table_creation.ipynb",
    "05_plot_creation.ipynb",
]


def test_phase_notebooks_exist_and_are_valid() -> None:
    notebooks_dir = Path("notebooks")
    for name in EXPECTED_NOTEBOOKS:
        path = notebooks_dir / name
        assert path.exists(), f"Missing notebook: {path}"
        nb = nbformat.read(path, as_version=4)
        assert nb.cells[0].cell_type == "markdown"
        assert "notebook_utils" in nb.cells[1].source


def test_phase2_query_plan_notebook_inspects_queries() -> None:
    nb = nbformat.read("notebooks/02_1_pubmed_query_plan.ipynb", as_version=4)
    source = "\n".join(cell.source for cell in nb.cells if cell.cell_type == "code")
    assert "materialize_phase_2_query_plan" in source
    assert "phase2_query_template_text" in source
    assert "phase2_concrete_query_example_text" in source
    assert "phase2_concrete_query_example_metadata" in source
    assert "wrap_text" in source
    assert "width=100" in source
    assert "phase2_query_plan_preview" in source
    assert "phase2_synonym_distribution_summary" in source
    assert "plot_phase2_synonym_distribution" in source
    assert source.index("materialize_phase_2_query_plan") < source.index("phase2_query_plan_preview")


def test_phase1_notebook_shows_live_progress_controls() -> None:
    nb = nbformat.read("notebooks/01_species_synonyms.ipynb", as_version=4)
    source = "\n".join(cell.source for cell in nb.cells if cell.cell_type == "code")
    assert "notebook_phase_parameters(\"phase1_species_synonyms\"" in source
    assert "FORCE_REFRESH = bool(PARAMS[\"force_refresh\"])" in source
    assert "RESUME = bool(PARAMS[\"resume\"])" in source
    assert "ENRICH_TAXONOMY_HIERARCHY = PARAMS.get(\"enrich_taxonomy_hierarchy\")" in source
    assert "phase1_progress_status" in source
    assert "enrich_taxonomy_hierarchy=bool(ENRICH_TAXONOMY_HIERARCHY)" in source
    assert "acquire_lpsn_species_records(" in source
    assert "normalise_lpsn_records(" in source
    assert "write_release_outputs(" in source


def test_phase2_acquisition_notebooks_default_to_not_running_network() -> None:
    pmid_nb = nbformat.read("notebooks/02_2_pubmed_acquire_pmids.ipynb", as_version=4)
    abstract_nb = nbformat.read("notebooks/02_3_pubmed_acquire_abstracts.ipynb", as_version=4)
    pmid_source = "\n".join(cell.source for cell in pmid_nb.cells if cell.cell_type == "code")
    abstract_source = "\n".join(cell.source for cell in abstract_nb.cells if cell.cell_type == "code")
    assert "notebook_phase_parameters(\"phase2_pmid_acquisition\"" in pmid_source
    assert "RUN_PUBMED_SEARCH = bool(PARAMS[\"run_pubmed_search\"])" in pmid_source
    assert "FORCE_REFRESH = bool(PARAMS[\"force_refresh\"])" in pmid_source
    assert "LOG_EVERY = int(PARAMS[\"log_every\"])" in pmid_source
    assert "phase2_pmid_acquisition_preflight" in pmid_source
    assert "run_phase_2_pmid_acquisition" in pmid_source
    assert "notebook_phase_parameters(\"phase2_abstract_fetch\"" in abstract_source
    assert "RUN_ABSTRACT_FETCH = bool(PARAMS[\"run_abstract_fetch\"])" in abstract_source
    assert "prepare_phase2_search_results" in abstract_source
    assert "selected_pmids_from_search_results" in abstract_source
    assert "run_pubmed_fetches(" in abstract_source
    assert "max_pmids=max_pmids" in abstract_source
    assert "raw_article_records_already_appended=True" in abstract_source
    assert "fetch_preflight" in abstract_source


def test_configure_pipeline_notebook_saves_yaml_parameters() -> None:
    nb = nbformat.read("notebooks/00_configure_pipeline.ipynb", as_version=4)
    source = "\n".join(cell.source for cell in nb.cells if cell.cell_type == "code")
    assert "NOTEBOOK_CONFIG_PATH = \"configs/notebook_parameters.yaml\"" in source
    assert "save_notebook_parameters" in source
    assert "phase1_species_synonyms" in source
    assert "phase2_pmid_acquisition" in source
    assert "\"provider\": \"openrouter_jev\"" in source
    assert "\"model\": \"typesafe/jev-1.14\"" in source


def test_phase3_notebook_exposes_provider_and_run_controls() -> None:
    nb = nbformat.read("notebooks/03_abstract_decisions.ipynb", as_version=4)
    source = "\n".join(cell.source for cell in nb.cells if cell.cell_type == "code")
    assert "notebook_phase_parameters(\"phase3_abstract_decisions\"" in source
    assert "phase3_preflight(PARAMS)" in source
    assert "run_phase3(PARAMS" in source
    assert nbutils.DEFAULT_NOTEBOOK_PARAMETERS["phase3_abstract_decisions"]["provider"] == "openrouter_jev"
    assert nbutils.DEFAULT_NOTEBOOK_PARAMETERS["phase3_abstract_decisions"]["model"] == "typesafe/jev-1.14"


def test_phase_notebooks_remain_thin() -> None:
    for path in Path("notebooks").glob("0*.ipynb"):
        nb = nbformat.read(path, as_version=4)
        code = "\n".join(cell.source for cell in nb.cells if cell.cell_type == "code")
        assert "def " not in code
        assert "class " not in code


def test_metadata_display_helpers_return_dataframes() -> None:
    metadata = {
        "source": "fixture",
        "record_counts": {"rows": 2},
        "mapping_diagnostics": {"duplicate_rows": 0},
        "package_versions": {"pandas": "fixture"},
        "outputs": {"missing": "/tmp/pathogen-atlas-v2-missing-file"},
        "v1_comparison": {"available": True, "v1_rows": 10},
    }
    summary = nbutils.phase_run_summary(metadata)
    assert list(summary) == ["overview", "counts", "package_versions", "outputs"]
    assert summary["overview"].iloc[0]["field"] == "source"
    assert set(summary["counts"]["section"]) == {"record_counts", "mapping_diagnostics"}
    assert nbutils.metadata_v1_comparison(metadata).iloc[0]["metric"] == "available"


def test_metadata_counts_explains_not_run_reason() -> None:
    counts = nbutils.metadata_counts({"status": "not_run", "reason": "Missing config"})

    assert counts.iloc[0]["section"] == "run_status"
    assert counts.iloc[0]["metric"] == "not_run_reason"
    assert counts.iloc[0]["value"] == "Missing config"


def test_output_summaries_include_row_counts(tmp_path: Path) -> None:
    parquet_path = tmp_path / "table.parquet"
    jsonl_path = tmp_path / "records.jsonl"
    json_path = tmp_path / "metadata.json"
    pd.DataFrame([{"a": 1}, {"a": 2}]).to_parquet(parquet_path, index=False)
    jsonl_path.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n', encoding="utf-8")
    json_path.write_text('{"record_counts": {"rows": 2}}\n', encoding="utf-8")

    outputs = nbutils.existing_outputs({"parquet": parquet_path, "jsonl": jsonl_path, "json": json_path})

    assert dict(zip(outputs["name"], outputs["rows"])) == {"parquet": 2, "jsonl": 3, "json": 1}
    assert "size_bytes" in outputs.columns


def test_phase2_concrete_query_example_prefers_synonyms(tmp_path: Path) -> None:
    query_plan = tmp_path / "pubmed_query_plan.yaml"
    query_plan.write_text(
        """
queries:
  - query: '"Solo species"[Title/Abstract]'
    canonical_species: Solo species
    synonym_count: 1
    matched_names:
      - Solo species
  - query: '"Synonym species"[Title/Abstract] OR "Canonical species"[Title/Abstract]'
    canonical_species: Canonical species
    synonym_count: 2
    matched_names:
      - Synonym species
      - Canonical species
  - query: '"Rich synonym one"[Title/Abstract] OR "Rich synonym two"[Title/Abstract] OR "Rich canonical"[Title/Abstract]'
    canonical_species: Rich canonical
    synonym_count: 3
    matched_names:
      - Rich synonym one
      - Rich synonym two
      - Rich canonical
""",
        encoding="utf-8",
    )
    query = nbutils.phase2_concrete_query_example_text(query_plan, prefer_synonyms=True)
    assert "Rich synonym one" in query
    assert "Rich canonical" in query

    summary = nbutils.phase2_synonym_distribution_summary(query_plan)
    histogram = nbutils.phase2_synonym_distribution_histogram(query_plan)
    assert summary.loc[0, "canonical_species"] == 3
    assert summary.loc[0, "max_synonyms"] == 2
    assert set(histogram["synonym_count"]) == {0, 1, 2}
    assert "histogram" in histogram.columns
    fig = nbutils.plot_phase2_synonym_distribution(query_plan)
    assert fig.axes[0].get_xlabel() == "Number of synonyms"
