# Notebooks

Use notebooks for inspection, quality control, and exploratory analysis only.

Reusable logic belongs in `pathogen_atlas_v2/`.

Notebook sequence:

1. `01_species_synonyms.ipynb`
2. `02_abstract_acquisition.ipynb`
3. `03_llm_extraction_vectorisation.ipynb`
4. `04_table_creation.ipynb`
5. `05_plot_creation.ipynb`

Each notebook is a thin orchestration layer: set parameters, invoke the matching Python pipeline, and inspect outputs.

Install notebook dependencies before opening the sequence:

```bash
python -m pip install -e ".[notebooks]"
```
