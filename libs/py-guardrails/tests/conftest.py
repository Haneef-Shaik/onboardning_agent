"""Shared fixtures: in-memory telemetry (onb_telemetry), plus a fake LLM and an in-memory audit log helper."""

from onb_telemetry.testing import telemetry

__all__ = ["telemetry"]
