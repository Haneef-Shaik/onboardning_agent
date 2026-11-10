"""Errors raised by the connector layer. Messages name config keys and env vars, never their values."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for every error this library raises."""


class LLMConfigError(LLMError):
    """`llm.yaml`, a selection or a connector's params are wrong."""


class ConnectorRegistryError(LLMError):
    """An installed connector package is broken or clashes with another."""


class ConnectorNotFoundError(LLMConfigError):
    """The configuration names a connector that no installed package provides."""


class ConnectorEnvironmentError(LLMConfigError):
    """The selected connector needs environment variables that are not set."""

    def __init__(self, connector: str, missing: tuple[str, ...], selected_by: tuple[str, ...]) -> None:
        self.connector = connector
        self.missing = missing
        self.selected_by = selected_by
        names = ", ".join(missing)
        verb = "is" if len(missing) == 1 else "are"
        super().__init__(
            f"LLM connector '{connector}' is selected (by {', '.join(selected_by)}) but {names} {verb} not set. "
            f"Set {names} in the environment or .env (see .env.example), or select another connector."
        )
