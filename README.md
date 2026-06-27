# Pathogen Atlas V2

This directory contains the staged data acquisition, extraction, table-building, and plotting pipeline for Pathogen Atlas version 2.

The project is organised around five phases:

1. Species synonym acquisition and normalisation.
2. Abstract acquisition using word filters.
3. LLM extraction and vectorisation.
4. Table creation.
5. Plot creation.

See [docs/organization.md](docs/organization.md) for the detailed structure and responsibilities.

## Setup

Install the package and all notebook/test dependencies from the repository root:

```bash
python -m pip install -e ".[all]"
```

For runtime-only use:

```bash
python -m pip install -e .
```

V1 plotting code used `matplotlib`, `plotly`, `networkx`, and scikit-learn
embedding/layout helpers. V2 includes `matplotlib` by default for notebook
figures and exposes the broader plotting stack through:

```bash
python -m pip install -e ".[viz]"
```

`pygraphviz` is intentionally not installed by default because it requires a
system Graphviz installation; add it locally only for Graphviz-backed network
layouts.

Phase 1 can be run with:

```bash
python pathogen_atlas_v2/pipelines/01_build_species_synonyms.py
```

or, after installation:

```bash
pathogen-atlas-phase1
```

Live LPSN acquisition requires ignored local credentials in `configs/local/lpsn_credentials.yaml`.
Phase 1 writes ambiguous synonym mappings to
`data/01_species/release/species_mapping_conflicts.parquet` instead of failing
the release export after a long acquisition run.

Phase 2 can be run with:

```bash
python pathogen_atlas_v2/pipelines/02_acquire_abstracts.py
```

or, after installation:

```bash
pathogen-atlas-phase2
```

PubMed acquisition requires ignored local NCBI settings in `configs/local/ncbi_entrez.yaml`.
Phase 2 renders one query per canonical species and PubMed field, OR-ing all
known synonyms for that canonical species before collecting and sampling PMIDs.
The notebook path is split into four steps: inspect queries, acquire all PMIDs,
fetch selected abstracts, and inspect table stats. The PMID and abstract
acquisition notebooks are resumable and skip work already written to disk.
