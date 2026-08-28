from scripts.check_docs import check_markdown_links, markdown_paths


def test_all_project_markdown_parses_and_local_links_resolve() -> None:
    assert len(markdown_paths()) >= 10
    check_markdown_links()
