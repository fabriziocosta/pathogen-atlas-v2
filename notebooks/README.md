# Notebooks

Use notebooks to expose pipeline parameters, show the concrete work invoked,
and inspect its outputs. Core scientific and API logic lives in
`pathogen_atlas_v2/` or, for static web-data lookups, in
`sites/pathogen-atlas-explorer/scripts/`.

Notebook sequence:

0. `00_configure_pipeline.ipynb`
1. `01_species_synonyms.ipynb`
2. `02_1_pubmed_query_plan.ipynb`
3. `02_2_pubmed_acquire_pmids.ipynb`
4. `02_3_pubmed_acquire_abstracts.ipynb`
5. `02_4_pubmed_table_stats.ipynb`
6. `03_abstract_decisions.ipynb`
7. `04_table_creation.ipynb`
8. `05_plot_creation.ipynb`
9. `06_wikipedia_references.ipynb`
10. `07_gbif_references.ipynb`

Pipeline notebooks are thin orchestration layers: set parameters, invoke the
matching Python pipeline, and inspect outputs. Phases 6 and 7 resolve
resumable Wikipedia and GBIF associations used to build the web explorer
dataset. Phase 7 follows the GBIF name matcher described in the [GBIF API
beginner guide](https://data-blog.gbif.org/post/gbif-api-beginners-guide/),
using its batch API for the full species list.
Shared notebook parameters are configured in `00_configure_pipeline.ipynb`
and saved to `configs/notebook_parameters.yaml`.

Install notebook dependencies before opening the sequence:

```bash
python -m pip install -e ".[notebooks]"
```
