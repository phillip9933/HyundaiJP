"""Install development and actual bundled integration runtime dependencies."""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Use pip argument lists, never interpolate dependency strings in a shell."""
    manifest = json.loads(
        (ROOT / "custom_components/hyundai_jp/manifest.json").read_text()
    )
    requirements = manifest["requirements"]
    if not isinstance(requirements, list) or not all(
        isinstance(requirement, str)
        and not requirement.startswith("-")
        and "hyundai_kia_connect_api" not in requirement.lower().replace("-", "_")
        for requirement in requirements
    ):
        raise ValueError(
            "Manifest must declare runtime dependencies, without external API"
        )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(ROOT / "requirements-dev.txt"),
            *requirements,
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
