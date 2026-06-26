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

Phase 1 can be run with:

```bash
python pathogen_atlas_v2/pipelines/01_build_species_synonyms.py
```

or, after installation:

```bash
pathogen-atlas-phase1
```

Live LPSN acquisition requires ignored local credentials in `configs/local/lpsn_credentials.yaml`.
