# Pathogen Atlas Explorer

A static Plotly explorer built from the Phase 4 release and the Phase 5 IMSTE
species coordinates. It runs in the browser with precomputed data and does not
need a Python server or database after publication.

The map has exactly one point per species, using that species’ saved Phase 5
coordinates, and a domain-expert feature profile. Use
**Color species by** to select any of the 73 feature prevalences or taxonomy
ranks from domain through genus. Open **Color settings** to choose a continuous
**Feature colormap** for numeric features, reverse it, or switch species-circle
edges between white and black (black by default). The colormap and reverse
controls are disabled for categorical taxonomy colors.
When a feature is selected, the chart hint shows its definition from the
versioned Jev feature set in `configs/domain_expert_questions.yaml`.
Use **Min supporting PMIDs** to hide species with fewer accepted pathogen-supporting PubMed records; the map count updates to reflect the filtered set. The default minimum is zero.
Search by species or taxonomy, zoom and pan the map, and click a point to
inspect the lineage, its Phase 1 synonyms, and the PMIDs accepted by the Phase 3
human pathogen evidence threshold. Taxonomy and synonyms appear side by side in
the species profile. When an exact English Wikipedia article or redirect exists,
a **Wikipedia** link appears beside the species name; the lookup table records
the resolved title and URL for every plotted species, including species with no
matching article. Expand **Pathogen supporting abstracts** to load its PMID
list; each PMID opens its PubMed record. PMID data is split into small hashed
shards and fetched only when the section is expanded, then cached by the
browser. The initial page load contains the single species coordinate and
feature record per point, not every abstract.
The header’s PubMed search-record total sums the per-species search results, so
a PMID returned for more than one species is counted in each species search.
Each supporting PMID includes the Jev confidence (`p_yes`) in its details.
Confidence is right-aligned beside the PMID, and the list can be sorted by
newest publication date or highest confidence.
The species profile also includes all 73 domain-expert features in a compact
three-column layout, grouped into bordered category boxes such as
transmission and patient. Each bar shows the share of scored abstracts
answered yes and the scored yes/total count; select a feature to read its
definition and color the map by it. The collapsible **Abstracts supporting
selected feature** section lists the accepted pathogen-evidence PMIDs that were
answered yes for the selected feature, using the same confidence and sorting
controls as the pathogen-supporting PMID list. Features without scored answers
are marked as unscored.
Each selected species shows its V2 **Pathogenic Interest Index**. Enable
**Size by Pathogenic Interest Index** to scale point area by its normalized score; clear it to
return to uniform point sizes.
The map opens with **Salmonella enterica** selected and colored by the
Epidemiology feature, with reversed Inferno and Pathogenic Interest Index
sizing enabled.
To open a species profile directly, append `?species=` and the URL-encoded
species name to the explorer URL, for example `?species=Escherichia%20coli`.
If a requested name is not in the explorer dataset, the map shows a message
instead of selecting a different species.

## Build the static data

Build the Phase 4 tables and Phase 5 plots first. Phase 5 writes the exact
coordinates used by `species_feature_space.png` to
`data/05_plots/release/species_feature_space_coordinates.parquet`. Then create
or refresh the Wikipedia and GBIF taxon lookup tables, then create the browser
dataset:

```bash
jupyter nbconvert --to notebook --execute notebooks/06_wikipedia_references.ipynb --output /tmp/06_wikipedia_references.ipynb
jupyter nbconvert --to notebook --execute notebooks/07_gbif_references.ipynb --output /tmp/07_gbif_references.ipynb
python sites/pathogen-atlas-explorer/scripts/build_data.py
```

Each notebook calls its corresponding lookup script and previews the saved
association table. Both reuse completed species rows; set `FORCE_REFRESH` to
`True` in a notebook to redo every lookup.

`build_wikipedia_links.py` queries the English Wikipedia API in small batches,
follows title normalization and redirects, and resumes rows already written to
`sites/pathogen-atlas-explorer/data/species_wikipedia_links.csv`. It records
only exact title/redirect matches, not fuzzy search results. The CSV has one row
per coordinate species and an empty URL when no article exists.

`build_gbif_links.py` matches species names against the GBIF Backbone using the
domain as a taxonomy hint. Following the [GBIF API beginner guide](https://data-blog.gbif.org/post/gbif-api-beginners-guide/), it uses the species name matcher to obtain taxon keys; the resolver batches names with the current v2 API for efficiency. It resumes cached results and adds a GBIF taxon page only for an exact, species-rank name match; unmatched names have an empty URL rather than a potentially incorrect taxon link.

This writes `sites/pathogen-atlas-explorer/dist/explorer-data.json` from the
accepted evidence, taxonomy, feature matrix, and saved IMSTE coordinates. It
also writes compact species-keyed files under `dist/evidence/`; these contain
only accepted PubMed IDs, titles, years, and DOI values. The build stops if
the coordinate table has duplicate species or the evidence table has no
acceptance flag, and includes the `pathogenic_index.parquet` data table for Pathogenic Interest Index marker sizing.
The payload includes total PubMed search records and the Wikipedia and GBIF URLs
from their lookup tables. The build also copies the repository logo into `dist/assets/` for
the explorer header. Rebuild it after updating the Phase 4 tables or rerunning
the IMSTE embedding.

## Preview locally

Serve the `dist` folder over HTTP so the page can fetch its JSON data:

```bash
python -m http.server 8000 --directory sites/pathogen-atlas-explorer/dist
```

Open <http://localhost:8000>. Plotly is loaded from its CDN; the site data
itself is a static JSON file. Publish the `dist` directory as a static site.
