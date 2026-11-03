"""Basic checks for the Pathogen Atlas V2 package."""


def test_scaffold_imports() -> None:
    import pathogen_atlas_v2

    assert pathogen_atlas_v2.__version__ == "2.0.1"
