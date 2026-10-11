"""Validate the release wheel, source distribution, metadata, and matching contents."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def inspect_distributions(directory: Path, tag: str | None = None) -> dict:
    wheels = list(directory.glob("*.whl"))
    sdists = list(directory.glob("*.tar.gz"))
    require(len(wheels) == len(sdists) == 1, "Expected one wheel and one sdist")
    wheel, sdist = wheels[0], sdists[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate wheel archive path")
        wheel_files = {name: archive.read(name) for name in names if not name.endswith("/")}
    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getmembers()
        require(all(m.isfile() or m.isdir() for m in members), "Unexpected sdist link")
        roots = {PurePosixPath(m.name).parts[0] for m in members}
        require(len(roots) == 1, "Expected a single sdist root")
        root = next(iter(roots))
        sdist_files: dict[str, bytes] = {}
        for member in members:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts, "Unsafe sdist path")
            if member.isfile():
                relative = path.relative_to(root).as_posix()
                require(relative not in sdist_files, "Duplicate sdist archive path")
                handle = archive.extractfile(member)
                require(handle is not None, "Missing sdist content")
                sdist_files[relative] = handle.read()

    metadatas = [name for name in wheel_files if name.endswith(".dist-info/METADATA")]
    require(len(metadatas) == 1, "Expected one wheel metadata file")
    wheel_metadata = BytesParser().parsebytes(wheel_files[metadatas[0]])
    source_metadata = BytesParser().parsebytes(sdist_files["PKG-INFO"])
    project = tomllib.loads(sdist_files["pyproject.toml"].decode())["project"]
    version = wheel_metadata["Version"]
    for key in ("Name", "Version", "Requires-Python", "License-Expression"):
        require(wheel_metadata[key] == source_metadata[key], f"Wheel/sdist differ: {key}")
    require(wheel_metadata["Name"] == project["name"] == "autospan", "Unexpected package name")
    require(version == project["version"], "Version differs from pyproject.toml")
    require(wheel_metadata["Requires-Python"] == ">=3.11", "Unexpected Python requirement")
    require(wheel_metadata["License-Expression"] == "MIT", "Missing SPDX license metadata")
    require("LICENSE" in wheel_metadata.get_all("License-File", []), "Missing license declaration")
    require(wheel_metadata["Description-Content-Type"] == "text/markdown", "Invalid README type")
    require(wheel_metadata["Author"] == "Nidhal Abidi", "Unexpected author metadata")
    require(
        wheel_metadata.get_payload().strip() == sdist_files["README.md"].decode().strip(),
        "Built README does not match source distribution",
    )
    require(wheel.name == f"autospan-{version}-py3-none-any.whl", "Unexpected wheel filename")
    require(sdist.name == f"autospan-{version}.tar.gz", "Unexpected source filename")
    if tag is not None:
        require(tag == f"v{version}", f"Tag {tag!r} differs from v{version}")

    urls = wheel_metadata.get_all("Project-URL", [])
    for label in ("Homepage", "Documentation", "Source", "Issues"):
        require(
            any(value.startswith(f"{label}, https://github.com/nidops/autospan") for value in urls),
            f"Missing project URL: {label}",
        )
    require(
        set(wheel_metadata.get_all("Provides-Extra", [])) == {"tui", "test", "dev"},
        "Extras mismatch",
    )
    info = metadatas[0].split("/")[0]
    require(
        all(p.startswith(("autospan/", f"{info}/")) for p in wheel_files),
        "Wheel has unexpected top-level paths",
    )
    require(wheel_files[f"{info}/licenses/LICENSE"] == sdist_files["LICENSE"], "License mismatch")
    require("autospan/py.typed" in wheel_files, "Missing PEP 561 typing marker")
    require("autospan/__main__.py" in wheel_files, "Missing Python CLI entry point")
    require(
        any(
            line.startswith("autospan = autospan.frontends.cli:main")
            for line in wheel_files[f"{info}/entry_points.txt"].decode().splitlines()
        ),
        "Missing console script",
    )
    package_files = {k: v for k, v in wheel_files.items() if k.startswith("autospan/")}
    source_files = {
        k.removeprefix("src/"): v for k, v in sdist_files.items() if k.startswith("src/autospan/")
    }
    require(package_files == source_files, "Wheel package bytes differ from source distribution")
    for path in ("README.md", "LICENSE", "RELEASING.md", "VALIDATION.md"):
        require(path in sdist_files, f"Missing source file {path}")
    for prefix in ("docs/", "scripts/", "tests/"):
        require(any(k.startswith(prefix) for k in sdist_files), f"Missing sdist files in {prefix}")
    for path in (*wheel_files, *sdist_files):
        parts = set(PurePosixPath(path).parts)
        require(
            not parts & {".github", ".git", ".venv", "artifacts", "__pycache__", "dist", "build"},
            f"Generated or private file in distribution: {path}",
        )
        require(
            not path.endswith((".pyc", ".pyo", ".local.yaml", ".local.yml")), f"Unexpected: {path}"
        )
    return {
        "name": "autospan",
        "version": version,
        "wheel_files": len(wheel_files),
        "sdist_files": len(sdist_files),
        "artifacts": [
            {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in (wheel, sdist)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("dist"))
    parser.add_argument("--tag", help="Require vX.Y.Z matching distribution metadata")
    args = parser.parse_args()
    print(json.dumps(inspect_distributions(args.directory, args.tag), indent=2))


if __name__ == "__main__":
    main()
