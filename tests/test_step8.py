"""Step 8 — Verifica configurazione Docker Compose (file esistenza + struttura)."""
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def test_docker_compose_file_exists():
    assert (ROOT / "docker-compose.yml").exists()


def test_docker_compose_valid_yaml():
    with open(ROOT / "docker-compose.yml") as f:
        data = yaml.safe_load(f)
    assert data is not None
    assert "services" in data


def test_docker_compose_has_visionapi_service():
    with open(ROOT / "docker-compose.yml") as f:
        data = yaml.safe_load(f)
    assert "visionapi" in data["services"]


def test_docker_compose_has_volumes():
    with open(ROOT / "docker-compose.yml") as f:
        data = yaml.safe_load(f)
    svc = data["services"]["visionapi"]
    assert "volumes" in svc
    volume_strings = " ".join(str(v) for v in svc["volumes"])
    assert "cameras.yaml" in volume_strings
    assert "actions.yaml" in volume_strings
    assert "sequences.yaml" in volume_strings


def test_docker_compose_has_env_file():
    with open(ROOT / "docker-compose.yml") as f:
        data = yaml.safe_load(f)
    svc = data["services"]["visionapi"]
    assert "env_file" in svc


def test_docker_compose_has_healthcheck():
    with open(ROOT / "docker-compose.yml") as f:
        data = yaml.safe_load(f)
    svc = data["services"]["visionapi"]
    assert "healthcheck" in svc


def test_docker_compose_has_restart_policy():
    with open(ROOT / "docker-compose.yml") as f:
        data = yaml.safe_load(f)
    svc = data["services"]["visionapi"]
    assert svc.get("restart") == "unless-stopped"


def test_install_readme_exists():
    assert (ROOT / "install-readme.md").exists()


def test_install_readme_has_required_sections():
    text = (ROOT / "install-readme.md").read_text()
    required = [
        "Prerequisiti",
        "docker compose up",
        "health",
        "ARM",
        "Troubleshooting",
    ]
    for section in required:
        assert section.lower() in text.lower(), f"Sezione mancante: {section}"


def test_dockerignore_exists():
    assert (ROOT / ".dockerignore").exists()


def test_dockerignore_excludes_env():
    content = (ROOT / ".dockerignore").read_text()
    assert ".env" in content


def test_dockerfile_exists():
    assert (ROOT / "Dockerfile").exists()
