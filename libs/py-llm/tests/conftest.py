"""Shared fixtures: the in-memory telemetry fixture and a writer for throwaway `llm.yaml` files."""

from collections.abc import Callable
from pathlib import Path

import pytest
from onb_telemetry.testing import telemetry

__all__ = ["telemetry", "write_config"]

ConfigWriter = Callable[[str], Path]


@pytest.fixture
def write_config(tmp_path: Path) -> ConfigWriter:
    """Write `text` to a fresh `llm.yaml` under `tmp_path` and return its path."""

    def _write(text: str) -> Path:
        path = tmp_path / "llm.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    return _write
