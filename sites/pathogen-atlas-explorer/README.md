# Pathogen Atlas Explorer

A static Plotly explorer built from the Phase 4 release and the Phase 5 IMSTE
species coordinates. It runs in the browser with precomputed data and does not
need a Python server or database after publication.

The map has exactly one point per species, using that species’ saved Phase 5
coordinates, and a domain-expert feature profile. Use
**Color species by** to select any of the 73 feature prevalences or taxonomy
ranks from domain through genus. Choose a continuous **Feature colormap** for
numeric features and optionally reverse it; both controls are disabled for
categorical taxonomy colors.
When a feature is selected, the chart hint shows that concept’s definition
from the versioned Jev question set in `configs/domain_expert_questions.yaml`.
Search by species or taxonomy, zoom and pan the map, and click a point to
inspect the lineage and the PMIDs accepted by the Phase 3 human pathogen
evidence threshold. Each PMID opens its PubMed record. PMID data is split into
small hashed shards and fetched only when a species is selected, then cached by
the browser. The initial page load contains the single species coordinate and
feature record per point, not every abstract.
Each supporting PMID includes the Jev confidence (`p_yes`) in its details.
Confidence is right-aligned beside the PMID, and the list can be sorted by
newest publication date or highest confidence.
Enable **Size by pathogenic index** to scale point area with the normalized V1
index; clear it to return to uniform point sizes.

## Build the static data

Build the Phase 4 tables and Phase 5 plots first. Phase 5 writes the exact
coordinates used by `species_feature_space.png` to
`data/05_plots/release/species_feature_space_coordinates.parquet`. Then create
the browser dataset:

```bash
python sites/pathogen-atlas-explorer/scripts/build_data.py
```

This writes `sites/pathogen-atlas-explorer/dist/explorer-data.json` from the
accepted evidence, taxonomy, feature matrix, and saved IMSTE coordinates. It
also writes compact species-keyed files under `dist/evidence/`; these contain
only accepted PubMed IDs, titles, years, and DOI values. The build stops if
the coordinate table has duplicate species or the evidence table has no
acceptance flag, and includes `pathogenic_index.parquet` for marker sizing.
Rebuild it after updating the Phase 4 tables or rerunning the IMSTE embedding.

## Preview locally

Serve the `dist` folder over HTTP so the page can fetch its JSON data:

```bash
python -m http.server 8000 --directory sites/pathogen-atlas-explorer/dist
```

Open <http://localhost:8000>. Plotly is loaded from its CDN; the site data
itself is a static JSON file. Publish the `dist` directory as a static site.
