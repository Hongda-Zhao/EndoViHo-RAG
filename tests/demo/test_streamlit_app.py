from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parents[2]


def test_first_screen_is_offline_and_exposes_real_state_boundaries() -> None:
    app = AppTest.from_file(ROOT / "app" / "streamlit_app.py", default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Evidence Workbench"
    assert app.selectbox[0].label == "Example question"
    assert len(app.selectbox[0].options) == 4
    assert app.text_area[0].label == "Controlled-English question"
    assert app.button[0].label == "Run evidence trace"
    assert any("Real scientific activation is blocked" in item.value for item in app.warning)
    assert any("Fail-closed" in item.value for item in app.info)
