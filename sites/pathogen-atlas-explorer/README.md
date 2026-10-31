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
When a feature is selected, the chart hint shows its definition from the
versioned Jev feature set in `configs/domain_expert_questions.yaml`.
Use **Min supporting PMIDs** to hide species with fewer accepted pathogen-supporting PubMed records; the map count updates to reflect the filtered set. The default minimum is zero.
Search by species or taxonomy, zoom and pan the map, and click a point to
inspect the lineage, its Phase 1 synonyms, and the PMIDs accepted by the Phase 3
human pathogen evidence threshold. Taxonomy and synonyms appear side by side in
the species profile. Expand **Pathogen supporting abstracts** to load its PMID
list; each PMID opens its PubMed record. PMID data is split into small hashed
shards and fetched only when the section is expanded, then cached by the
browser. The initial page load contains the single species coordinate and
feature record per point, not every abstract.
Each supporting PMID includes the Jev confidence (`p_yes`) in its details.
Confidence is right-aligned beside the PMID, and the list can be sorted by
newest publication date or highest confidence.
The species profile also includes all 73 domain-expert features in a compact
three-column layout, grouped into bordered category boxes such as
transmission and patient. Each bar shows the share of scored abstracts
answered yes and the scored yes/total count; select a feature to read its
definition and color the map by it. Features without scored answers are marked
as unscored.
Each selected species shows its V2 pathogenic index. Enable **Size by
pathogenic index** to scale point area by its normalized score; clear it to
return to uniform point sizes.
The map opens colored by the Epidemiology feature with reversed Inferno and
pathogenic-index sizing enabled.

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
