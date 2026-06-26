"""Console entry point for Phase 2 PubMed abstract acquisition."""

from pathogen_atlas_v2.abstracts.abstract_exports import run_phase2


def main() -> None:
    """Run Phase 2 from an importable console-script module."""
    run_phase2()
