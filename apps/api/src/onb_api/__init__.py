"""HTTP API (FastAPI): every answer is the `{success, data, error, meta}` envelope (PRD §10)."""

from onb_api.app import create_app
from onb_api.errors import AppError
from onb_api.responses import respond

__all__ = ["PROJECT", "AppError", "create_app", "respond"]

PROJECT = "api"
