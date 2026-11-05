"""Build and verify the self-contained HACS integration ZIP, without importing HA."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "hyundai_jp"
VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:(?:a|b|rc)[1-9][0-9]*)?"
)
FORBIDDEN_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "tests",
    "private",
    ".git",
}
LICENSES = ("LICENSE", "LICENSE.api.txt")
PROVENANCE = "ORIGINS.json"


def validate_version(version: str) -> str:
    """Accept unambiguous release names, also safe for tags and filenames."""
    if not VERSION.fullmatch(version):
        raise ValueError(
            "Version must look like 1.2.3, 1.2.3b1 or 1.2.3rc1 (no v prefix)"
        )
    return version


def release_files(root: Path, version: str | None = None) -> dict[str, bytes]:
    """Collect only runtime component files and retain original license notices."""
    component = root / "custom_components" / DOMAIN
    files: dict[str, bytes] = {}
    for path in sorted(component.rglob("*")):
        relative = path.relative_to(component)
        if any(
            part in FORBIDDEN_PARTS or part.startswith(".") for part in relative.parts
        ):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinks cannot be packaged: {relative}")
        if not path.is_file() or path.suffix in {".pyc", ".pyo", ".whl"}:
            continue
        files[relative.as_posix()] = path.read_bytes()
    for name in (*LICENSES, PROVENANCE):
        files[name] = (root / name).read_bytes()
    manifest = json.loads(files["manifest.json"])
    if manifest["domain"] != DOMAIN or manifest["name"] != "Hyundai Bluelink JP":
        raise ValueError("Expected Hyundai Bluelink JP manifest with hyundai_jp domain")
    for requirement in manifest["requirements"]:
        if "hyundai_kia_connect_api" in requirement.lower().replace("-", "_"):
            raise ValueError(
                "Release cannot depend on an external regional API package"
            )
    manifest["version"] = validate_version(version or manifest["version"])
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    for required in (
        "__init__.py",
        "config_flow.py",
        "_api/__init__.py",
        "_api/KiaUvoApiJP.py",
    ):
        if required not in files:
            raise ValueError(f"Missing bundled runtime file: {required}")
    return files


def build(root: Path, output: Path, version: str | None = None) -> Path:
    """Write a deterministic ZIP, then verify every packaged byte and its checksum."""
    files = release_files(root, version)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{DOMAIN}.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zipped:
        for name, data in sorted(files.items()):
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zipped.writestr(info, data)
    with ZipFile(archive) as zipped:
        if zipped.testzip() or set(zipped.namelist()) != set(files):
            raise ValueError("Release archive did not round-trip")
        if any(zipped.read(name) != data for name, data in files.items()):
            raise ValueError("Release archive differs from bundled component")
    digest = sha256(archive.read_bytes()).hexdigest()
    (output / f"{DOMAIN}.zip.sha256").write_text(f"{digest}  {archive.name}\n")
    print(f"{digest}  {archive}")
    return archive


def main() -> None:
    """Allow release version stamping without changing checked-in source files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=validate_version)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    arguments = parser.parse_args()
    build(ROOT, arguments.output, arguments.version)


if __name__ == "__main__":
    main()
