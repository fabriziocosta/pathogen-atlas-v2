# Pathogen Atlas V2 Organization

Pathogen Atlas V2 is structured as a staged data pipeline. Each phase has a clear responsibility, its own code module, and a matching data area. The goal is to make every output reproducible, auditable, and easy to regenerate without mixing exploratory work with release artifacts.

## Top-Level Layout

```text
version2/
├── configs/
├── data/
├── docs/
├── notebooks/
├── pathogen_atlas_v2/
├── tests/
└── archive/
```

`configs/` contains versioned settings that control the pipeline: source lists, word filters, LLM questions, prompt templates, and plot specifications.

`data/` contains staged pipeline artifacts. The folders are numbered to make the execution order explicit.

`docs/` contains design notes and explanations of project organization.

`notebooks/` is reserved for inspection, quality control, and exploratory analysis. Reusable logic should live in `pathogen_atlas_v2/`, not in notebooks.

`pathogen_atlas_v2/` contains the Python package for reusable code and pipeline entry points.

`tests/` contains focused checks for parsing, filtering, table contracts, and expected output schemas.

`archive/` contains scratch work or deprecated experiments that should not be part of the normal V2 run.

## Phase 1: Species Synonym Acquisition And Normalisation

Purpose: acquire species names and synonyms from source lists, normalise taxonomic names, resolve accepted names, and produce stable mapping files for later phases.

Code lives in:

```text
pathogen_atlas_v2/species/
```

Data lives in:

```text
data/01_species/
├── raw/
├── interim/
└── release/
```

Expected release outputs:

```text
data/01_species/release/
├── species_canonical.parquet
├── species_synonym_mapping.parquet
├── species_taxonomy.parquet
└── species_mapping_metadata.json
```

The main rule for this phase is that downstream phases should consume the release parquet files rather than repeating synonym logic. This gives the project a single taxonomic source of truth.

Phase 1 is implemented as a live LPSN rebuild. It uses V1 mapping files only for diagnostic comparison and never treats them as authoritative inputs. Local LPSN credentials must be stored in ignored config:

```text
configs/local/lpsn_credentials.yaml
```

with this shape:

```yaml
lpsn:
  username: "..."
  password: "..."
```

Run the phase from the repository root with:

```bash
python pathogen_atlas_v2/pipelines/01_build_species_synonyms.py
```

If `data/01_species/raw/lpsn_species_records.jsonl` already exists, the pipeline uses that cache and does not require credentials or network access. Delete the raw cache or call the Python API with `force_refresh=True` to reacquire from LPSN.

## Phase 2: Abstract Acquisition

Purpose: acquire abstracts using species mappings and configurable word filters. This phase should preserve raw records, record filter matches, deduplicate abstracts, and produce a clean abstract corpus.

Code lives in:

```text
pathogen_atlas_v2/abstracts/
```

Data lives in:

```text
data/02_abstracts/
├── raw/
├── filtered/
└── release/
```

Expected release outputs:

```text
data/02_abstracts/release/
├── abstracts.parquet
├── abstract_species_matches.parquet
├── abstract_filter_matches.parquet
└── abstract_acquisition_metadata.json
```

Word filters should be defined in `configs/abstract_filters.yaml`. The filtering code should record which terms matched each abstract so later analyses can be audited.

## Phase 3: LLM Extraction And Vectorisation

Purpose: use LLMs to classify whether each species is a pathogen, answer structured microbiology questions, anonymise abstract text, and vectorise anonymised abstracts.

Code lives in:

```text
pathogen_atlas_v2/llm/
```

Data lives in:

```text
data/03_llm/
├── prompts/
├── raw_responses/
├── parsed/
├── anonymised/
├── vectors/
└── release/
```

Expected release outputs:

```text
data/03_llm/release/
├── pathogen_yes_no.parquet
├── microbiology_answers.parquet
├── anonymised_abstracts.parquet
├── abstract_vectors.parquet
└── llm_run_metadata.json
```

Raw responses and parsed responses are deliberately separate. If a parser changes, we should be able to rebuild parsed outputs without rerunning model calls. Prompts and question definitions should be versioned in `configs/llm_questions.yaml` and `configs/prompts/`.

## Phase 4: Table Creation

Purpose: combine species mappings, abstract counts, LLM-derived features, and emergence calculations into final analytical and web-facing tables.

Code lives in:

```text
pathogen_atlas_v2/tables/
```

Data lives in:

```text
data/04_tables/
├── interim/
└── release/
```

Expected release outputs:

```text
data/04_tables/release/
├── species.csv
├── counts.csv
├── counts_extended.csv
├── emergence.csv
├── pathogen_features.csv
├── abstract_evidence.csv
└── web_bundle/
```

This phase is the boundary between data production and downstream consumers. The web app, paper figures, and reports should use the release tables from this phase rather than pulling directly from raw or interim data.

## Phase 5: Plot Creation

Purpose: create publication, diagnostic, and web-oriented figures from release tables and vector outputs.

Code lives in:

```text
pathogen_atlas_v2/plots/
```

Data lives in:

```text
data/05_plots/
├── figures/
├── diagnostics/
└── release/
```

Expected release outputs:

```text
data/05_plots/release/
├── emergence/
├── taxonomy/
├── embeddings/
├── diagnostics/
└── figure_manifest.json
```

Plot code should consume stable release data, mainly from `data/04_tables/release/`. Diagnostic plots can use interim files when necessary, but those dependencies should be documented.

## Python Package Layout

```text
pathogen_atlas_v2/
├── common/
├── species/
├── abstracts/
├── llm/
├── tables/
├── plots/
└── pipelines/
```

`common/` holds shared I/O, schemas, logging, and validation helpers.

`species/` owns source loading, synonym handling, taxonomy normalisation, and mapping exports.

`abstracts/` owns query construction, abstract acquisition, word filtering, and deduplication.

`llm/` owns prompts, structured extraction, pathogen classification, anonymisation, vectorisation, and LLM response schemas.

`tables/` owns deterministic table construction and final export formats.

`plots/` owns deterministic figure generation.

`pipelines/` contains thin orchestration entry points for each phase. These scripts should call functions from the package rather than containing complex logic directly.

## Data Lifecycle

The intended data lifecycle is:

```text
raw -> interim/filtered/parsed -> release
```

Raw data should be preserved as acquired. Interim data can be regenerated. Release data is the stable contract between phases and with external consumers.

## Placeholder Policy

The current files are stubs. Each stub marks the intended responsibility of a module or data folder. Implementation should be added incrementally, with tests added whenever a module starts enforcing a schema, parser, filter, or table contract.
