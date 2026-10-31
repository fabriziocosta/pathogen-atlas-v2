# Data

Generated pipeline data belongs under the repository-root `data/` directory,
organized by phase:

1. `01_species`
2. `02_abstracts`
3. `03_decisions`
4. `04_tables`
5. `05_plots`

Each phase separates raw or intermediate artifacts from release outputs.

Parquet files in a phase's `release/` directory are its canonical generated
tables. CSVs and other web assets are derived exports with an explicit,
curated purpose. Never write phase data under `notebooks/`; notebook working
directories are not stable and must not determine where pipeline artifacts
are stored.
