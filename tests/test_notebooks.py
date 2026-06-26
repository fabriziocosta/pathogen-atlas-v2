"""Notebook structure checks."""

from __future__ import annotations

from pathlib import Path

import nbformat

from pathogen_atlas_v2 import notebook_utils as nbutils


EXPECTED_NOTEBOOKS = [
    "01_species_synonyms.ipynb",
    "02_abstract_acquisition.ipynb",
    "03_llm_extraction_vectorisation.ipynb",
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


def test_phase2_notebook_inspects_query_plan_before_acquisition() -> None:
    nb = nbformat.read("notebooks/02_abstract_acquisition.ipynb", as_version=4)
    source = "\n".join(cell.source for cell in nb.cells if cell.cell_type == "code")
    assert "materialize_phase_2_query_plan" in source
    assert "phase2_query_plan_preview" in source
    assert "RUN_ACQUISITION = False" in source
    assert source.index("materialize_phase_2_query_plan") < source.index("phase2_query_plan_preview")
    assert source.index("phase2_query_plan_preview") < source.index("run_phase_2")


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
