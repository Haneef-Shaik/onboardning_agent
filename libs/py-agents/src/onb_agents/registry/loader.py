"""Read `config/agents/*.yaml`: one manifest per file, named after the agent (`db_agent.yaml`)."""

from __future__ import annotations

from pathlib import Path

import yaml

from .manifest import AgentManifest, ManifestError, parse_manifest

__all__ = ["load_manifests", "read_yaml"]


def read_yaml(path: Path) -> object:
    """Parse one YAML file safely (no tags, no objects); errors name the file, not its content."""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ManifestError(f"{path.name}: cannot read it as YAML ({type(error).__name__})") from None


def load_manifests(directory: Path) -> tuple[AgentManifest, ...]:
    """Every manifest in `directory`, validated, sorted by name. One bad file fails the whole load."""
    if not directory.is_dir():
        raise ManifestError(f"{directory} is not a directory of agent manifests")
    manifests = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            manifest = parse_manifest(read_yaml(path))
        except ManifestError as error:
            message = str(error)
            prefix = "" if message.startswith(f"{path.name}:") else f"{path.name}: "
            raise ManifestError(f"{prefix}{message}") from None
        if manifest.name != path.stem:
            raise ManifestError(f"{path.name}: names the agent {manifest.name}; the file must be {manifest.name}.yaml")
        manifests.append(manifest)
    if not manifests:
        raise ManifestError(f"{directory} holds no agent manifests (*.yaml)")
    return tuple(manifests)
