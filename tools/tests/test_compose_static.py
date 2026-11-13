"""Static guards on the compose stack files (F-2; PRD §7.11). These run in `make check`, without Docker.

The live checks (healthy services, `docker compose config` ports, auth refusal, transactions) are in
tools/tests_int and libs/py-data/tests_int, run by `make test-int`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra" / "docker-compose.yml"
MAKEFILE = ROOT / "Makefile"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "path",
    [
        "infra/docker-compose.yml",
        "infra/mongo/rs-init.js",
        "infra/mongo/gen-keyfile.sh",
        "infra/otel/collector.yaml",
        "infra/otel/Dockerfile",
        "infra/caddy/Caddyfile.dev",
        ".env.example",
        "scripts/check_env_example.py",
    ],
)
def test_stack_file_exists(path: str) -> None:
    assert (ROOT / path).is_file(), path


PINNED = re.compile(r":\d+\.\d+\.\d+(\.\d+)?(-[a-z]+)?@sha256:[0-9a-f]{64}$")
LOCAL_BUILD = "onb/"  # images built from infra/ Dockerfiles, whose FROM lines are pinned instead


def test_pulled_images_are_pinned_by_version_and_digest() -> None:
    images = re.findall(r"^\s*image:\s*(\S+)", _text(COMPOSE), re.M)
    assert images
    for image in images:
        if image.startswith(LOCAL_BUILD):
            assert re.search(r":\d+\.\d+\.\d+$", image), image
            continue
        assert PINNED.search(image), f"{image} is not pinned to an exact version and digest"
    froms = [
        line.split()[1]
        for line in _text(ROOT / "infra" / "otel" / "Dockerfile").splitlines()
        if line.startswith("FROM")
    ]
    assert froms
    for image in froms:
        assert PINNED.search(image), image


def test_test_tunnel_image_is_pinned_by_digest() -> None:
    conftest = _text(ROOT / "libs" / "py-data" / "tests_int" / "conftest.py")
    image = re.search(r'SOCAT_IMAGE = "([^"]+)"', conftest)
    assert image
    assert PINNED.search(image.group(1)), image.group(1)


def test_mongo_runs_a_replica_set_with_auth_and_keyfile() -> None:
    compose = _text(COMPOSE)
    assert "--replSet" in compose
    assert "--keyFile" in compose
    assert "MONGO_INITDB_ROOT_USERNAME: ${MONGO_ROOT_USERNAME:?" in compose
    assert "MONGO_INITDB_ROOT_PASSWORD: ${MONGO_ROOT_PASSWORD:?" in compose
    assert "rs.initiate" in _text(ROOT / "infra" / "mongo" / "rs-init.js")


MONGO_RSEQ_TUNABLE = "glibc.pthread.rseq=1"


@pytest.mark.parametrize(
    "path",
    ["infra/docker-compose.yml", "apps/mcp-hub/tests_int/conftest.py", "apps/mcp-mongo/tests_int/conftest.py"],
)
def test_every_mongo_we_start_lets_glibc_own_rseq(path: str) -> None:
    """mongod 8.0 refuses to start on Linux 6.19+ kernels (SERVER-121912), which GitHub's runners use, unless
    glibc owns rseq. Every place that starts a Mongo container must keep the tunable until MongoDB ships a fix."""
    text = _text(ROOT / path)
    assert "GLIBC_TUNABLES" in text
    assert MONGO_RSEQ_TUNABLE in text


def test_keyfile_is_generated_and_ignored_never_committed() -> None:
    gen = _text(ROOT / "infra" / "mongo" / "gen-keyfile.sh")
    assert "openssl rand" in gen
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "infra/mongo/keyfile"], cwd=ROOT, check=False, capture_output=True
    )
    assert ignored.returncode == 0, "infra/mongo/keyfile must be git-ignored"
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "infra/mongo/keyfile"], cwd=ROOT, check=False, capture_output=True
    )
    assert tracked.returncode != 0, "the Mongo keyfile is committed"


def test_dev_caddyfile_protects_jaeger_with_basic_auth() -> None:
    caddyfile = _text(ROOT / "infra" / "caddy" / "Caddyfile.dev")
    assert "basic_auth" in caddyfile
    assert "reverse_proxy jaeger:16686" in caddyfile


def test_jaeger_credentials_never_travel_over_plain_http() -> None:
    caddyfile = _text(ROOT / "infra" / "caddy" / "Caddyfile.dev")
    assert "http://jaeger.localhost" not in caddyfile
    assert "https://jaeger.localhost" in caddyfile


def test_unknown_hosts_get_a_404() -> None:
    caddyfile = _text(ROOT / "infra" / "caddy" / "Caddyfile.dev")
    assert re.search(r"^:80 \{\n\trespond .* 404\n\}", caddyfile, re.M)


def _run_gen_keyfile(keyfile: Path) -> None:
    script = ROOT / "infra" / "mongo" / "gen-keyfile.sh"
    subprocess.run(["sh", str(script), str(keyfile)], check=True, capture_output=True)


def test_gen_keyfile_writes_a_private_keyfile(tmp_path: Path) -> None:
    keyfile = tmp_path / "keyfile"
    _run_gen_keyfile(keyfile)
    assert keyfile.stat().st_mode & 0o777 == 0o400
    assert len(keyfile.read_text().replace("\n", "")) >= 1000


def test_gen_keyfile_keeps_an_existing_key_but_repairs_its_mode(tmp_path: Path) -> None:
    keyfile = tmp_path / "keyfile"
    keyfile.write_text("existing-key")
    keyfile.chmod(0o644)
    _run_gen_keyfile(keyfile)
    assert keyfile.read_text() == "existing-key"
    assert keyfile.stat().st_mode & 0o777 == 0o400


def test_gen_keyfile_replaces_an_empty_read_only_keyfile(tmp_path: Path) -> None:
    keyfile = tmp_path / "keyfile"
    keyfile.touch()
    keyfile.chmod(0o400)
    _run_gen_keyfile(keyfile)
    assert keyfile.stat().st_size > 0


def test_collector_exports_traces_to_jaeger() -> None:
    config = _text(ROOT / "infra" / "otel" / "collector.yaml")
    assert "endpoint: jaeger:4317" in config
    assert "health_check" in config


@pytest.mark.parametrize("target", ["up", "down", "logs", "test-int"])
def test_makefile_has_stack_targets(target: str) -> None:
    assert re.search(rf"^{re.escape(target)}:", _text(MAKEFILE), re.M), f"Makefile lacks {target}"


def test_make_up_waits_for_health_and_generates_the_keyfile() -> None:
    makefile = _text(MAKEFILE)
    up = re.search(r"^up:.*?(?=^\S)", makefile, re.M | re.S)
    assert up
    assert "gen-keyfile.sh" in up.group(0)
    assert "up -d --wait" in up.group(0)


def test_dev_caddyfile_serves_the_web_app_and_the_api_on_localhost() -> None:
    """UI-1: the browser reaches the web dev server at `/` and the API under `/api` (prefix stripped)."""
    caddyfile = _text(ROOT / "infra" / "caddy" / "Caddyfile.dev")
    site = _site_block(caddyfile, "https://localhost")
    assert re.search(r"handle_path /api/\* \{\n\t\treverse_proxy \{\$API_DEV_UPSTREAM\}", site)
    assert re.search(r"\thandle \{\n\t\treverse_proxy \{\$WEB_DEV_UPSTREAM\}", site)
    assert site.index("handle /healthz") < site.index("handle {"), "/healthz must stay ahead of the web fallback"


def test_the_app_and_api_are_not_served_over_plain_http() -> None:
    """Passwords and bearer tokens never cross the network in clear text; HTTP keeps /healthz only."""
    caddyfile = _text(ROOT / "infra" / "caddy" / "Caddyfile.dev")
    plain = _site_block(caddyfile, "http://localhost")
    assert "reverse_proxy" not in plain
    assert 'respond "ok" 200' in plain
    assert not re.search(r"^http://localhost,", caddyfile, re.M), "the app site must not include plain HTTP"


def _site_block(caddyfile: str, address: str) -> str:
    match = re.search(rf"^{re.escape(address)} \{{\n(.*?)^\}}$", caddyfile, re.M | re.S)
    assert match, f"no site block for {address}"
    return match.group(1)


def test_caddy_can_reach_dev_servers_on_the_host() -> None:
    compose = _text(ROOT / "infra" / "docker-compose.yml")
    caddy = compose.split("\n  caddy:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    assert "host.docker.internal:host-gateway" in caddy
    assert "API_DEV_UPSTREAM: ${API_DEV_UPSTREAM:-host.docker.internal:" in caddy
    assert "WEB_DEV_UPSTREAM: ${WEB_DEV_UPSTREAM:-host.docker.internal:" in caddy
