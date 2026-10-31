# Pathogen Atlas Explorer

A static Plotly explorer built from the Phase 4 release and the Phase 5 IMSTE
species coordinates. It runs in the browser with precomputed data and does not
need a Python server or database after publication.

The map has one point per species with a domain-expert feature profile. Use
**Color species by** to select any of the 73 feature prevalences or taxonomy
ranks from domain through genus. Search by species or taxonomy, zoom and pan
the map, and click a point to inspect the lineage and all accepted evidence
PMIDs. Each PMID opens its PubMed record.

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
includes the accepted PubMed IDs, titles, years, and DOI values. Rebuild it
after updating the Phase 4 tables or rerunning the IMSTE embedding.

## Preview locally

Serve the `dist` folder over HTTP so the page can fetch its JSON data:

```bash
python -m http.server 8000 --directory sites/pathogen-atlas-explorer/dist
```

Open <http://localhost:8000>. Plotly is loaded from its CDN; the site data
itself is a static JSON file. Publish the `dist` directory as a static site.
