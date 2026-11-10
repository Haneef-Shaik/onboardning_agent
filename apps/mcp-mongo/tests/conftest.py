"""Fixtures: the in-memory telemetry exporter and a fake database."""

from __future__ import annotations

import pytest
from onb_telemetry.testing import telemetry

from .fakes import FakeDatabase

__all__ = ["telemetry"]


@pytest.fixture
def db() -> FakeDatabase:
    return FakeDatabase()
