"""Verify wheel/sdist installation from outside the checkout and optional index hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import venv
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile


def run(args: list[str], cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("dist"))
    parser.add_argument("--distribution", choices=("wheel", "sdist"), default="wheel")
    parser.add_argument("--index-url", help="Verify an index wheel has the exact expected SHA-256")
    args = parser.parse_args()
    directory = args.directory.resolve()
    wheels, sdists = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        parser.error("Expected one wheel and one sdist")
    if args.index_url and args.distribution != "wheel":
        parser.error("Index verification supports wheels only")
    wheel, sdist = wheels[0], sdists[0]
    with ZipFile(wheel) as archive:
        (metadata_file,) = (p for p in archive.namelist() if p.endswith(".dist-info/METADATA"))
        version = BytesParser().parsebytes(archive.read(metadata_file))["Version"]

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    for key in list(env):
        if key.startswith("PIP_"):
            env.pop(key)
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    print(
        f"Testing {args.distribution} on {sys.platform} with Python {sys.version.split()[0]}",
        flush=True,
    )

    with tempfile.TemporaryDirectory(prefix="autospan-release-") as temp:
        root = Path(temp)
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        pip = [str(python), "-I", "-m", "pip"]
        run([*pip, "install", "--upgrade", "pip"], root, env)

        with tarfile.open(sdist, "r:gz") as archive:
            archive.extractall(root / "source", filter="data")
        (source,) = (root / "source").iterdir()
        distribution = wheel if args.distribution == "wheel" else sdist
        if args.index_url:
            download = root / "download"
            download.mkdir()
            command = [
                *pip,
                "download",
                "--no-cache-dir",
                "--no-deps",
                "--only-binary=:all:",
                "--index-url",
                args.index_url,
                "--dest",
                str(download),
                f"autospan=={version}",
            ]
            for attempt in range(5):
                result = subprocess.run(command, cwd=root, env=env)
                if result.returncode == 0:
                    break
                if attempt == 4:
                    result.check_returncode()
                time.sleep(5)
            (downloaded,) = download.glob("*.whl")
            if (
                hashlib.sha256(downloaded.read_bytes()).digest()
                != hashlib.sha256(wheel.read_bytes()).digest()
            ):
                raise ValueError("Index distribution SHA-256 differs from validated wheel")
            distribution = downloaded

        # Check the minimal package before optional frontend dependencies can mask
        # an undeclared import or a broken generated console entry point.
        run([*pip, "install", "--no-cache-dir", str(distribution)], root, env)
        run([*pip, "check"], root, env)
        run(
            [
                str(python),
                "-I",
                "-c",
                "import importlib.util; import autospan.api; "
                "assert importlib.util.find_spec('pytest') is None; "
                "assert importlib.util.find_spec('textual') is None",
            ],
            root,
            env,
        )
        cli = python.with_name("autospan.exe" if os.name == "nt" else "autospan")
        run([str(cli), "--help"], root, env)
        run([str(cli), "plugins"], root, env)
        run([str(cli), "providers"], root, env)
        run([*pip, "install", "--no-cache-dir", f"{distribution}[test,tui]"], root, env)
        run([*pip, "check"], root, env)
        smoke = (
            "import sys, autospan; from pathlib import Path; "
            "from importlib.metadata import version, entry_points; "
            f"assert autospan.__version__ == version('autospan') == {version!r}; "
            "assert Path(autospan.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "assert Path(autospan.__file__).with_name('py.typed').is_file(); "
            "assert __import__('autospan.api'); "
            "print('Installed AutoSpan:', autospan.__file__)"
        )
        run([str(python), "-I", "-c", smoke], root, env)
        run([str(python), "-I", "-m", "autospan", "plugins"], root, env)
        run([str(python), "-I", "-m", "autospan", "providers"], root, env)

        plugin_root = root / "smoke"
        plugin_root.mkdir()
        run(
            [
                str(python),
                "-I",
                "-m",
                "autospan",
                "new",
                "release-smoke",
                "--dir",
                str(plugin_root),
            ],
            root,
            env,
        )
        generated = plugin_root / "autospan-release-smoke"
        run([*pip, "install", "--no-cache-dir", "-e", f"{generated}[dev]"], root, env)
        config = generated / "autospan.yaml"
        run([str(python), "-I", "-m", "autospan", "plan", "--config", str(config)], root, env)
        run(
            [str(python), "-I", "-m", "autospan", "test", "--config", str(config), "--", "-q"],
            root,
            env,
        )
        run(
            [
                str(python),
                "-I",
                "-m",
                "autospan",
                "console",
                "--config",
                str(config),
                "--plain",
                "--exec",
                "release-smoke status",
            ],
            root,
            env,
        )

        run([str(python), "-I", "-m", "pytest", "-q", "tests"], generated, env)
        failure_root = root / "intentional-failure"
        failed = subprocess.run(
            [
                str(cli),
                "test",
                "--config",
                str(config),
                "--set",
                "release-smoke.name=intentional-failure",
                "--evidence-dir",
                str(failure_root),
                "--",
                "-q",
            ],
            cwd=root,
            env=env,
        )
        if failed.returncode != 1:
            raise ValueError(
                f"Intentional scenario failure returned {failed.returncode}, expected 1"
            )
        (manifest_path,) = failure_root.glob("*/manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["outcome"] != "failed" or manifest["metadata"]["pytest_exit_code"] != 1:
            raise ValueError("Intentional failure did not produce a failed evidence manifest")

        # Pytest must not resolve 'src/' from the unpacked source by configuration.
        # All core imports should originate from the installed distribution.
        run(
            [
                str(python),
                "-I",
                "-m",
                "pytest",
                "--import-mode=importlib",
                "-o",
                "pythonpath=",
                "-o",
                "addopts=-ra --strict-markers",
                "tests",
            ],
            source,
            env,
        )


if __name__ == "__main__":
    main()
