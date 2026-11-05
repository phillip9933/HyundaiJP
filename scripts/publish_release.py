"""Publish an already checked commit once, using the runner's GitHub token."""

import json
import os
from pathlib import Path
import re
import subprocess

from scripts.build_release import ROOT, build, validate_version


def gh(*arguments: str) -> str:
    """Pass arguments directly to the GitHub CLI, without invoking a shell."""
    return subprocess.run(
        ["gh", *arguments], check=True, capture_output=True, text=True
    ).stdout


def publish(version: str, prerelease: bool, commit: str, output: Path) -> None:
    """Create a fresh tag, upload to a draft, then publish its complete assets."""
    validate_version(version)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Release target must be a full commit SHA")
    if not prerelease and re.search(r"(?:a|b|rc)[0-9]+$", version):
        raise ValueError("A development version must be marked as a prerelease")
    tag = f"v{version}"
    # Query before changing anything. Failures (including permission/network
    # errors) fail closed rather than being mistaken for a missing release.
    releases = json.loads(
        gh("api", "--paginate", "--slurp", "repos/{owner}/{repo}/releases")
    )
    if any(release["tag_name"] == tag for page in releases for release in page):
        raise ValueError(f"Release {tag} already exists; publish a new version")
    refs = json.loads(
        gh("api", f"repos/{{owner}}/{{repo}}/git/matching-refs/tags/{tag}")
    )
    if any(ref["ref"] == f"refs/tags/{tag}" for ref in refs):
        raise ValueError(f"Tag {tag} already exists; publish a new version")
    archive = build(ROOT, output, version)
    notes = output / "release-notes.md"
    notes.write_text(
        f"HyundaiJP {version}\n\n"
        "Install or update through HACS. The Japan API is included.\n\n"
        "Download hyundai_jp.zip for a manual installation. "
        "The SHA-256 checksum is attached.\n"
    )
    # Creating the ref fails if another publisher claimed it after preflight.
    # No force update and no asset overwrite: prior releases remain untouched.
    gh(
        "api",
        "repos/{owner}/{repo}/git/refs",
        "-X",
        "POST",
        "-f",
        f"ref=refs/tags/{tag}",
        "-f",
        f"sha={commit}",
    )
    arguments = [
        "release",
        "create",
        tag,
        "--verify-tag",
        "--draft",
        "--title",
        tag,
        "--notes-file",
        str(notes),
        str(archive),
        str(output / "hyundai_jp.zip.sha256"),
    ]
    if prerelease:
        arguments.append("--prerelease")
    gh(*arguments)
    # Publishing only after asset upload also supports GitHub release immutability.
    gh(
        "release",
        "edit",
        tag,
        "--draft=false",
        f"--latest={str(not prerelease).lower()}",
    )
    print(f"Published {tag} from {commit}")


def main() -> None:
    """Read untrusted dispatch inputs as data and validate before use."""
    prerelease = os.environ["RELEASE_PRERELEASE"]
    if prerelease not in {"true", "false"}:
        raise ValueError("RELEASE_PRERELEASE must be true or false")
    publish(
        os.environ["RELEASE_VERSION"],
        prerelease == "true",
        os.environ["RELEASE_COMMIT"],
        ROOT / "dist",
    )


if __name__ == "__main__":
    main()
