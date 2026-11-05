"""Dependency selection has one pinned HA version and never invokes a shell."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from scripts import install_dependencies


@pytest.fixture
def repository(tmp_path):
    component = tmp_path / "custom_components/hyundai_jp"
    component.mkdir(parents=True)
    (component / "manifest.json").write_text(
        json.dumps({"requirements": ["requests==2.32.5", "Pillow==12.1.1"]})
    )
    (tmp_path / "hacs.json").write_text(json.dumps({"homeassistant": "2026.9.2"}))
    (tmp_path / "requirements-dev.txt").write_text(
        "# Reproducible tools, with HA selected separately\npytest==9.1.1\n"
    )
    return tmp_path


@pytest.mark.parametrize(
    "selected,expected", [(None, "2026.9.2"), ("2026.11.0", "2026.11.0")]
)
def test_install_command_selects_only_one_pinned_ha(repository, selected, expected):
    command = install_dependencies.install_command(repository, selected)
    assert command[:4] == [install_dependencies.sys.executable, "-m", "pip", "install"]
    assert command[4:6] == ["-r", str(repository / "requirements-dev.txt")]
    assert [part for part in command if part.startswith("homeassistant")] == [
        f"homeassistant=={expected}"
    ]
    assert command[-2:] == ["requests==2.32.5", "Pillow==12.1.1"]


@pytest.mark.parametrize(
    "invalid",
    [
        "2026.11",
        "2026.11.0\n",
        ">=2026.11.0",
        "--pre",
        "2026.11.0;echo bad",
        2026,
        None,
    ],
)
def test_invalid_pins_are_rejected_before_pip(repository, invalid):
    if invalid is None:
        (repository / "hacs.json").write_text(json.dumps({"homeassistant": None}))
    with pytest.raises(ValueError, match="pinned"):
        install_dependencies.install_command(repository, invalid)


@pytest.mark.parametrize(
    "duplicate",
    [
        "homeassistant==2026.9.2",
        "HomeAssistant>=2026.9.2",
        "homeassistant[extra]==2026.9.2",
    ],
)
def test_development_tools_cannot_silently_add_a_second_ha_pin(repository, duplicate):
    (repository / "requirements-dev.txt").write_text(f"pytest==9.1.1\n{duplicate}\n")
    with pytest.raises(ValueError, match="separately"):
        install_dependencies.install_command(repository, "2026.11.0")


@pytest.mark.parametrize(
    "requirements",
    [
        None,
        "requests==2.32.5",
        [""],
        [" --index-url"],
        ["--pre"],
        ["homeassistant==2026.9.2"],
        ["hyundai-kia-connect-api==1.0.0"],
        ["requests==2.32.5\n--pre"],
        [42],
    ],
)
def test_unsafe_or_external_api_runtime_requirements_are_rejected(
    repository, requirements
):
    (repository / "custom_components/hyundai_jp/manifest.json").write_text(
        json.dumps({"requirements": requirements})
    )
    with pytest.raises(ValueError, match="runtime dependencies"):
        install_dependencies.install_command(repository)


def test_main_passes_cli_pin_as_a_checked_argument_list(monkeypatch, repository):
    run = Mock()
    monkeypatch.setattr(install_dependencies, "ROOT", repository)
    monkeypatch.setattr(
        install_dependencies.sys,
        "argv",
        ["install_dependencies.py", "--homeassistant", "2026.11.0"],
    )
    monkeypatch.setattr(install_dependencies.subprocess, "run", run)
    install_dependencies.main()
    run.assert_called_once_with(
        install_dependencies.install_command(repository, "2026.11.0"), check=True
    )


def test_ci_exercises_minimum_and_current_ha_with_complete_branch_coverage():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load(
        (root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"]["offline-checks"]
    minimum = json.loads((root / "hacs.json").read_text())["homeassistant"]
    assert job["strategy"]["matrix"]["homeassistant"] == [minimum, "2026.11.0"]
    setup = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/setup-python@")
    )
    assert setup["with"]["python-version"] == "3.14.8"
    regression = next(
        step for step in job["steps"] if step.get("name") == "Offline regression tests"
    )
    assert "--cov=custom_components.hyundai_jp" in regression["run"]
    assert "--cov-branch" in regression["run"]
    assert "--cov-fail-under=80" in regression["run"]
    assert "--cov-report=xml:coverage.xml" in regression["run"]
    assert "--cov-report=json:coverage.json" in regression["run"]
    assert "omit" not in regression["run"]
    artifact = next(
        step for step in job["steps"] if step.get("name") == "Preserve coverage reports"
    )
    assert artifact["if"] == "always()"
    assert "matrix.homeassistant" in artifact["with"]["name"]
    assert artifact["with"]["path"].splitlines() == ["coverage.xml", "coverage.json"]
