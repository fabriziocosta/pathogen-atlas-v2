# Pathogen Atlas V2

This directory contains the staged data acquisition, extraction, table-building, and plotting pipeline for Pathogen Atlas version 2.

The project is organised around five phases:

1. Species synonym acquisition and normalisation.
2. Abstract acquisition using word filters.
3. Abstract decision sources and vectorisation.
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

The `viz` extra installs the pinned [IMSTE sister project](https://github.com/fabriziocosta/IteratedMinimumSpanningTree). Phase 5 standardizes the species-by-domain-feature matrix, fits an IMSTE embedding, and colors the same map by the most variable feature prevalences. Adjust the `imste_*` settings in `configs/notebook_parameters.yaml` or the `IMSTE_PARAMETERS` block in Notebook 00. The estimator method, tree count, neighbor counts, optimization epochs, negative ratio, repulsion share, learning rate, device, and random seed are configurable; defaults use FAMST with a fixed random seed.

See [docs/embedding_methodology.md](docs/embedding_methodology.md) for the full feature aggregation, preprocessing, graph construction, coordinate optimization, and interpretation details.

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

Phase 3 scores selected species–PMID abstract pairs with Jev System 1. Set
`OPENROUTER_API_KEY`, review the question in `configs/llm_questions.yaml`, then
open `notebooks/03_abstract_decisions.ipynb`. It defaults to a 25-call pilot;
raise `max_calls` or set it to `null` in `configs/notebook_parameters.yaml` to
allow the full pending run. Results are stored as raw responses under
`data/03_decisions/raw_responses/jev/` and resumable parsed decisions under
`data/03_decisions/parsed/`. The source records probabilities only; it does not
apply a production threshold. The CLI entry point is `pathogen-atlas-phase3`;
it only runs when `run_phase: true` is set in the shared Phase 3 parameters.

## Open the explorer

On macOS, double-click [`launch_explorer.command`](launch_explorer.command) in
the V2 root. It starts a local web server on an available port and opens the
interactive explorer in your browser. Keep the Terminal window open while
using the page; press Ctrl+C there to stop the server.
