"""Parse project Markdown and reject broken or escaping local links."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).parents[1].resolve()


def markdown_paths() -> tuple[Path, ...]:
    return tuple(
        sorted(
            {
                ROOT / "README.md",
                ROOT / "CHANGELOG.md",
                *(ROOT / "docs").glob("*.md"),
                ROOT / "data" / "README.md",
            }
        )
    )


def check_markdown_links() -> None:
    parser = MarkdownIt("commonmark")
    for path in markdown_paths():
        text = path.read_text(encoding="utf-8")
        for token in parser.parse(text):
            href = token.attrGet("href") if token.type == "link_open" else None
            if href is None:
                continue
            parts = urlsplit(href)
            if parts.scheme or parts.netloc or href.startswith("#"):
                continue
            target_text = unquote(parts.path)
            if not target_text:
                continue
            target = (path.parent / target_text).resolve()
            if not target.is_relative_to(ROOT):
                raise RuntimeError(f"{path.relative_to(ROOT)} link escapes the repository: {href}")
            if not target.exists():
                raise RuntimeError(f"{path.relative_to(ROOT)} has a broken local link: {href}")


def main() -> int:
    check_markdown_links()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
