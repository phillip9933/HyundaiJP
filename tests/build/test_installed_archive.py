"""Smoke test the exact release payload without the source checkout on sys.path."""

import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from scripts.build_release import build


def test_installed_zip_imports_all_platforms_without_external_api(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    archive = build(root, tmp_path / "dist")
    install_root = tmp_path / "ha/config"
    component = install_root / "custom_components/hyundai_jp"
    component.mkdir(parents=True)
    with ZipFile(archive) as zipped:
        zipped.extractall(component)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(install_root)
    environment["HYUNDAIJP_SOURCE_ROOT"] = str(root)
    # The child starts outside the checkout and reads only the extracted ZIP.
    # Block the old API explicitly even if a developer has it installed locally.
    script = """
import importlib
import importlib.abc
import os
from pathlib import Path
import socket
import sys

installed = Path.cwd().resolve()
source_root = Path(os.environ["HYUNDAIJP_SOURCE_ROOT"])
assert all(not Path(path).resolve().is_relative_to(source_root) for path in sys.path)

class BlockExternalApi(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "hyundai_kia_connect_api" or fullname.startswith("hyundai_kia_connect_api."):
            raise ModuleNotFoundError("External API imports are forbidden in HyundaiJP")
        return None

sys.meta_path.insert(0, BlockExternalApi())

def no_network(*args, **kwargs):
    raise AssertionError("Release import smoke test cannot use the network")

socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.socket.sendto = no_network
socket.getaddrinfo = no_network
socket.create_connection = no_network
component = installed / "custom_components/hyundai_jp"
modules = []
for source in sorted(component.rglob("*.py")):
    relative = source.relative_to(installed).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    name = ".".join(parts)
    imported = importlib.import_module(name)
    assert Path(imported.__file__).resolve().is_relative_to(component)
    modules.append(name)
assert "custom_components.hyundai_jp.config_flow" in modules
assert "custom_components.hyundai_jp._api.KiaUvoApiJP" in modules
assert "custom_components.hyundai_jp.lock" in modules
assert not any(name.startswith("hyundai_kia_connect_api") for name in sys.modules)
print(f"Imported {len(modules)} bundled modules from extracted release")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=install_root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "bundled modules from extracted release" in completed.stdout
