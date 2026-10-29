# Phase 5: Plots

Phase 5 reads only Phase 4 release Parquet tables and makes reproducible figures under `release/`. Figures reproduce the Version 1 style emergence view and feature-profile overview, and add accepted evidence counts by species, yearly publication counts, and taxonomy composition. The feature-profile projection is a deterministic two-component PCA over the V1 73-question species prevalence matrix; it does not assert or assign clusters. `plot_creation_metadata.json` records figure and source paths. Rerunning this phase does not refetch articles or repeat Jev calls.
