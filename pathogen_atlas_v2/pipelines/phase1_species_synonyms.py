"""Console entry point for Phase 1 species synonym rebuild."""

import importlib


def main() -> None:
    """Run Phase 1 from an importable console-script module."""
    module = importlib.import_module("pathogen_atlas_v2.pipelines.01_build_species_synonyms")
    module.main()
