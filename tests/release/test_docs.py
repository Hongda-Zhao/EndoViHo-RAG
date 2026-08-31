from pathlib import Path

from scripts.check_docs import ROOT, check_markdown_links, markdown_paths


def test_all_project_markdown_parses_and_local_links_resolve() -> None:
    paths = set(markdown_paths())
    assert {
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "data" / "README.md",
        ROOT / "docs" / "data_semantics.md",
    } <= paths
    assert all(isinstance(path, Path) for path in paths)
    check_markdown_links()
