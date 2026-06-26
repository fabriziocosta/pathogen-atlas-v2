"""Notebook structure checks."""

from __future__ import annotations

from pathlib import Path

import nbformat


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


def test_phase_notebooks_remain_thin() -> None:
    for path in Path("notebooks").glob("0*.ipynb"):
        nb = nbformat.read(path, as_version=4)
        code = "\n".join(cell.source for cell in nb.cells if cell.cell_type == "code")
        assert "def " not in code
        assert "class " not in code
