from pathlib import Path


def test_pi_alexandria_uses_pep_420_namespace_layout() -> None:
    """Keep the publisher and product namespace portions initializer-free."""
    project_root = Path(__file__).parents[3]
    assert (project_root / "src" / "pi" / "alexandria").is_dir()
    assert not (project_root / "src" / "pi" / "__init__.py").exists()
    assert not (project_root / "src" / "pi" / "alexandria" / "__init__.py").exists()
