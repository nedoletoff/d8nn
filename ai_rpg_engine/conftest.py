"""Shared pytest configuration and fixtures.

The ``pythonpath`` setting below puts the project root on ``sys.path`` so tests
can ``import engine...`` / ``import ui...`` without installing the package.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent / "data"


@pytest.fixture
def seeded_data(tmp_path: Path) -> Path:
    """A throwaway copy of ``data/`` (base catalogs + templates + empty worlds)."""
    data = tmp_path / "data"
    data.mkdir()
    for source in DATA_DIR.glob("*.json"):
        shutil.copy(source, data / source.name)
    shutil.copytree(DATA_DIR / "templates", data / "templates")
    (data / "worlds").mkdir()
    return data
