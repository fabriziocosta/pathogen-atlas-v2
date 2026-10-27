"""Phase 3: run configured abstract decision sources."""

from pathogen_atlas_v2.llm.pathogen_evidence import phase3_preflight, run_phase3
from pathogen_atlas_v2.notebook_utils import notebook_phase_parameters


def main() -> dict[str, object]:
    """Run Phase 3 using the shared notebook/runtime configuration."""
    config = notebook_phase_parameters("phase3_abstract_decisions")
    if not bool(config.get("run_phase", False)):
        result = {
            "status": "not_run",
            "reason": "Set run_phase: true in the Phase 3 notebook parameters to call Jev.",
            "preflight": phase3_preflight(config),
        }
        print(result)
        return result
    result = run_phase3(config)
    print(result)
    return result


if __name__ == "__main__":
    print(main())
