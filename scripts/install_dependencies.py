"""Install development and actual bundled integration runtime dependencies."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def install_command(root: Path, homeassistant: str | None = None) -> list[str]:
    """Select exactly one pinned HA version and validated runtime dependencies."""
    version = (
        homeassistant
        if homeassistant is not None
        else json.loads((root / "hacs.json").read_text())["homeassistant"]
    )
    if not isinstance(version, str) or not re.fullmatch(
        r"20\d{2}\.\d{1,2}\.\d{1,2}", version
    ):
        raise ValueError("Home Assistant must be a pinned YYYY.M.P release")
    development = root / "requirements-dev.txt"
    if any(
        re.match(r"homeassistant(?:\W|$)", line.strip(), flags=re.IGNORECASE)
        for line in development.read_text().splitlines()
    ):
        raise ValueError("Select Home Assistant separately from development tools")
    manifest = json.loads(
        (root / "custom_components/hyundai_jp/manifest.json").read_text()
    )
    requirements = manifest["requirements"]
    if not isinstance(requirements, list) or not all(
        isinstance(requirement, str)
        and bool(requirement.strip())
        and requirement == requirement.strip()
        and not any(character.isspace() for character in requirement)
        and not requirement.startswith("-")
        and not re.match(r"homeassistant(?:\W|$)", requirement, flags=re.IGNORECASE)
        and "hyundai_kia_connect_api" not in requirement.lower().replace("-", "_")
        for requirement in requirements
    ):
        raise ValueError(
            "Manifest must declare runtime dependencies, without external API"
        )
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(development),
        f"homeassistant=={version}",
        *requirements,
    ]


def main() -> None:
    """Use pip argument lists, never interpolate dependency strings in a shell."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--homeassistant",
        help="Pinned HA release to test; defaults to the HACS minimum",
    )
    arguments = parser.parse_args()
    subprocess.run(install_command(ROOT, arguments.homeassistant), check=True)


if __name__ == "__main__":
    main()
