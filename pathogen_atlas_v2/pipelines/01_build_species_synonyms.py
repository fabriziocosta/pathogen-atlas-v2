"""Phase 1: build species synonym mappings."""

from pathogen_atlas_v2.species.acquire import SpeciesAcquisitionError
from pathogen_atlas_v2.species.export_mappings import run_phase1


def main() -> None:
    """Run the species synonym pipeline."""
    try:
        run_phase1()
    except SpeciesAcquisitionError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
