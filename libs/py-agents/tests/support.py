"""Shared paths for py-agents tests."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = ROOT / "config" / "agents"
