# Notebooks

Use notebooks for inspection, quality control, and exploratory analysis only.

Reusable logic belongs in `pathogen_atlas_v2/`.

Notebook sequence:

0. `00_configure_pipeline.ipynb`
1. `01_species_synonyms.ipynb`
2. `02_1_pubmed_query_plan.ipynb`
3. `02_2_pubmed_acquire_pmids.ipynb`
4. `02_3_pubmed_acquire_abstracts.ipynb`
5. `02_4_pubmed_table_stats.ipynb`
6. `03_llm_extraction_vectorisation.ipynb`
7. `04_table_creation.ipynb`
8. `05_plot_creation.ipynb`

Each notebook is a thin orchestration layer: set parameters, invoke the matching Python pipeline, and inspect outputs.
Shared notebook parameters are configured in `00_configure_pipeline.ipynb`
and saved to `configs/notebook_parameters.yaml`.

Install notebook dependencies before opening the sequence:

```bash
python -m pip install -e ".[notebooks]"
```
