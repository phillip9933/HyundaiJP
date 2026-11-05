"""Verify installable HACS archives and fail-closed release publication offline."""

from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
import yaml

from scripts import build_release, publish_release


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A component tree includes data files, unwanted files and vendored sources."""
    component = tmp_path / "custom_components/hyundai_jp"
    (component / "_api").mkdir(parents=True)
    (component / "translations").mkdir()
    for name in (
        "__init__.py",
        "config_flow.py",
        "_api/__init__.py",
        "_api/KiaUvoApiJP.py",
    ):
        (component / name).write_text(f"# exact runtime bytes: {name}\n")
    (component / "translations/en.json").write_text('{"title": "HyundaiJP"}')
    (component / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "hyundai_jp",
                "name": "HyundaiJP",
                "version": "0.1.0b1",
                "requirements": ["requests>=2.32"],
            }
        )
    )
    (component / "__pycache__").mkdir()
    (component / "__pycache__/__init__.cpython-314.pyc").write_bytes(
        b"not runtime source"
    )
    (component / "private").mkdir()
    (component / "private/account.txt").write_text("never include private files")
    (component / "unrelated.whl").write_bytes(b"not a bundled dependency")
    for name in (*build_release.LICENSES, build_release.PROVENANCE):
        (tmp_path / name).write_text(f"MIT attribution: {name}\n")
    return tmp_path


def test_archive_can_install_without_source_checkout(
    repository: Path, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    archive = build_release.build(repository, output, "0.2.0b2")
    expected_api = repository / "custom_components/hyundai_jp/_api/KiaUvoApiJP.py"
    with ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {
            "__init__.py",
            "config_flow.py",
            "manifest.json",
            "_api/__init__.py",
            "_api/KiaUvoApiJP.py",
            "translations/en.json",
            "LICENSE",
            "LICENSE.api.txt",
            "ORIGINS.json",
        }
        assert zipped.read("_api/KiaUvoApiJP.py") == expected_api.read_bytes()
        assert json.loads(zipped.read("manifest.json"))["version"] == "0.2.0b2"
        zipped.extractall(tmp_path / "ha/config/custom_components/hyundai_jp")
    assert (
        tmp_path / "ha/config/custom_components/hyundai_jp/config_flow.py"
    ).is_file()
    assert (
        json.loads(
            (repository / "custom_components/hyundai_jp/manifest.json").read_text()
        )["version"]
        == "0.1.0b1"
    )
    assert (
        output / "hyundai_jp.zip.sha256"
    ).read_text() == f"{sha256(archive.read_bytes()).hexdigest()}  hyundai_jp.zip\n"
    original_bytes = archive.read_bytes()
    assert (
        build_release.build(repository, output, "0.2.0b2").read_bytes()
        == original_bytes
    )


@pytest.mark.parametrize(
    "version",
    ["v1.0.0", "1.0", "1.0.0\n", "../bad", "1.0.0;echo bad", "01.2.3", "1.0.0b0"],
)
def test_invalid_version_never_invokes_github(
    version: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    github = Mock()
    monkeypatch.setattr(publish_release, "gh", github)
    with pytest.raises(ValueError):
        publish_release.publish(version, True, "a" * 40, tmp_path)
    github.assert_not_called()


def test_external_api_dependency_cannot_ship(repository: Path) -> None:
    manifest = repository / "custom_components/hyundai_jp/manifest.json"
    data = json.loads(manifest.read_text())
    data["requirements"] = ["hyundai-kia-connect-api==1.0.0"]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="external"):
        build_release.release_files(repository)


def test_missing_api_cannot_ship(repository: Path) -> None:
    (repository / "custom_components/hyundai_jp/_api/KiaUvoApiJP.py").unlink()
    with pytest.raises(ValueError, match="Missing bundled"):
        build_release.release_files(repository)


@pytest.mark.parametrize("existing_release", [True, False])
def test_published_versions_are_never_overwritten(
    existing_release: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    github = Mock(
        side_effect=[
            json.dumps([[{"tag_name": "v0.1.0b1"}]] if existing_release else [[]]),
            json.dumps([{"ref": "refs/tags/v0.1.0b1"}]),
        ]
    )
    monkeypatch.setattr(publish_release, "gh", github)
    with pytest.raises(ValueError, match="already exists"):
        publish_release.publish("0.1.0b1", True, "a" * 40, tmp_path)
    assert all(
        "POST" not in call.args and "create" not in call.args
        for call in github.call_args_list
    )


@pytest.mark.parametrize("version,prerelease", [("0.1.0b1", True), ("0.1.0", False)])
def test_release_uploads_assets_before_publishing(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    prerelease: bool,
) -> None:
    github = Mock(side_effect=["[[]]", "[]", "{}", "", ""])
    monkeypatch.setattr(publish_release, "gh", github)
    monkeypatch.setattr(publish_release, "ROOT", repository)
    publish_release.publish(version, prerelease, "a" * 40, tmp_path / "dist")
    calls = [call.args for call in github.call_args_list]
    assert "sha=" + "a" * 40 in calls[2]
    assert calls[3][:3] == ("release", "create", f"v{version}")
    assert "--draft" in calls[3] and "--verify-tag" in calls[3]
    assert ("--prerelease" in calls[3]) is prerelease
    assert any(argument.endswith("hyundai_jp.zip") for argument in calls[3])
    assert any(argument.endswith("hyundai_jp.zip.sha256") for argument in calls[3])
    assert calls[4] == (
        "release",
        "edit",
        f"v{version}",
        "--draft=false",
        f"--latest={str(not prerelease).lower()}",
    )


def test_workflows_keep_checks_and_safe_release_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    workflows = [
        yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for path in (root / ".github/workflows").glob("*.yml")
    ]
    for workflow in workflows:
        assert "pull_request_target" not in workflow["on"]
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert len(step["uses"].split("@")[1]) == 40
                assert "${{ inputs." not in step.get("run", "")
    ci = yaml.load(
        (root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(ci["jobs"]["build"]["needs"]) == {"offline-checks", "hassfest", "hacs"}
    release = yaml.load(
        (root / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert release["jobs"]["publish"]["needs"] == "checks"
    assert release["concurrency"]["cancel-in-progress"] == "false"
    assert (
        release["on"]["workflow_dispatch"]["inputs"]["prerelease"]["default"] == "false"
    )
