from pathlib import Path

from scripts.check_docs import ROOT, check_markdown_links, markdown_paths


def test_all_project_markdown_parses_and_local_links_resolve() -> None:
    paths = set(markdown_paths())
    assert {
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "data" / "README.md",
        ROOT / "docs" / "data_semantics.md",
        ROOT / "docs" / "repository" / "README.cn.md",
        ROOT / "docs" / "repository" / "README.ja.md",
    } <= paths
    assert all(isinstance(path, Path) for path in paths)
    check_markdown_links()


def test_repository_readmes_expose_consistent_language_switches() -> None:
    expected_first_lines = {
        ROOT / "README.md": (
            "**English** | [简体中文](docs/repository/README.cn.md) | "
            "[日本語](docs/repository/README.ja.md)"
        ),
        ROOT / "docs" / "repository" / "README.cn.md": (
            "[English](../../README.md) | **简体中文** | [日本語](README.ja.md)"
        ),
        ROOT / "docs" / "repository" / "README.ja.md": (
            "[English](../../README.md) | [简体中文](README.cn.md) | **日本語**"
        ),
    }
    for path, expected in expected_first_lines.items():
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == expected
