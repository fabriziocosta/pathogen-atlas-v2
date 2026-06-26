"""Phase 2: acquire and filter abstracts."""

from pathogen_atlas_v2.abstracts.abstract_exports import run_phase2
from pathogen_atlas_v2.abstracts.pubmed_client import PubMedClientError
from pathogen_atlas_v2.abstracts.pubmed_query_plan import PubMedQueryPlanError


def main() -> None:
    """Run the abstract acquisition pipeline."""
    try:
        run_phase2()
    except (PubMedClientError, PubMedQueryPlanError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
