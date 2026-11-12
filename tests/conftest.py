import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def load_json(relative: str):
    return json.loads((FIXTURES / relative).read_text("utf-8"))
