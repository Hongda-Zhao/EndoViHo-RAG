from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
DEMO_ROOT = ROOT / "src" / "eve_relation_rag" / "demo"
FORBIDDEN_IMPORT_PREFIXES = (
    "sqlalchemy",
    "psycopg",
    "eve_relation_rag.bootstrap",
    "eve_relation_rag.application",
    "eve_relation_rag.generation",
    "eve_relation_rag.retrieval",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_demo_has_no_database_provider_cli_or_tests_only_import() -> None:
    paths = sorted(DEMO_ROOT.glob("*.py")) + [ROOT / "app" / "streamlit_app.py"]
    imports = {module for path in paths for module in _imported_modules(path)}
    forbidden = {
        module
        for module in imports
        if module.startswith(FORBIDDEN_IMPORT_PREFIXES) or module.startswith("tests")
    }
    assert forbidden == set()


def test_demo_client_has_one_fixed_api_surface() -> None:
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in sorted(DEMO_ROOT.glob("*.py"))
    }
    assert sum(source.count('"/v0/query"') for source in sources.values()) == 1
    assert "EVE_RAG_DEMO_API_BASE_URL" in sources[DEMO_ROOT / "client.py"]
    assert all("subprocess" not in source for source in sources.values())
