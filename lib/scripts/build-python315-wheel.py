#!/usr/bin/env python3
"""Build the fastuuid stable-ABI wheel with CPython 3.12–3.14 and Rust."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import sysconfig
import tarfile
import urllib.request
import zipfile
from pathlib import Path

SOURCE_URL = "https://files.pythonhosted.org/packages/source/f/fastuuid/fastuuid-0.14.0.tar.gz"
SOURCE_SHA256 = "178947fc2f995b38497a74172adee64fdeb8b7ec18f2a5934d037641ba265d26"
VERSION = "0.14.0+blue.1"
MATURIN_VERSION = "1.15.0"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"unexpected upstream content in {path.name}")
    path.write_text(text.replace(old, new, 1))


def prepare(archive: Path, work: Path) -> Path:
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("fastuuid source checksum does not match")
    work.mkdir()
    with tarfile.open(archive) as source:
        members = source.getmembers()
        for member in members:
            parts = Path(member.name).parts
            if (
                not parts
                or parts[0] != "fastuuid-0.14.0"
                or ".." in parts
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError("unexpected fastuuid archive entry")
        source.extractall(work, members=members, filter="data")
    root = work / "fastuuid-0.14.0"
    replace_once(root / "Cargo.toml", 'version = "0.14.0"', f'version = "{VERSION}"')
    replace_once(
        root / "Cargo.toml",
        'features = ["extension-module"]',
        'features = ["extension-module", "abi3-py310"]',
    )
    replace_once(
        root / "Cargo.lock",
        'name = "fastuuid"\nversion = "0.14.0"',
        f'name = "fastuuid"\nversion = "{VERSION}"',
    )
    replace_once(root / "pyproject.toml", 'requires-python = ">=3.8"', 'requires-python = ">=3.10"')
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdist", type=Path, help="Use an already-downloaded upstream sdist")
    parser.add_argument("--work-dir", type=Path, required=True, help="New build directory")
    parser.add_argument("--wheel-dir", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    if (
        platform.python_implementation() != "CPython"
        or not (3, 12) <= sys.version_info[:2] < (3, 15)
        or sysconfig.get_config_var("Py_GIL_DISABLED")
    ):
        parser.error("build with regular CPython 3.12–3.14; install the resulting wheel on 3.15")
    try:
        installed_maturin = importlib.metadata.version("maturin")
    except importlib.metadata.PackageNotFoundError:
        installed_maturin = None
    if installed_maturin != MATURIN_VERSION:
        parser.error(f"install maturin=={MATURIN_VERSION} in the build environment")
    work = args.work_dir.resolve()
    wheels = args.wheel_dir.resolve()
    if work.exists() or wheels.exists():
        parser.error("work and wheel directories must not exist")
    if work == wheels or work in wheels.parents or wheels in work.parents:
        parser.error("work and wheel directories must be separate")
    work.parent.mkdir(parents=True, exist_ok=True)
    archive = args.sdist.resolve() if args.sdist else work.with_suffix(".tar.gz")
    if args.sdist is None:
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, archive.open("xb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
    source = prepare(archive, work)
    wheels.mkdir(parents=True)
    home = work / "build-home"
    home.mkdir()
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TMPDIR") if key in os.environ}
    env.update(
        HOME=str(home),
        CARGO_HOME=str(work / "cargo-home"),
        RUSTUP_HOME=os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
        CARGO_REGISTRIES_CRATES_IO_PROTOCOL="sparse",
        PYO3_PYTHON=sys.executable,
        PYTHONDONTWRITEBYTECODE="1",
    )
    if "RUSTUP_TOOLCHAIN" in os.environ:
        env["RUSTUP_TOOLCHAIN"] = os.environ["RUSTUP_TOOLCHAIN"]
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-m",
            "maturin",
            "build",
            "--release",
            "--locked",
            "--interpreter",
            sys.executable,
            "--out",
            str(wheels),
        ],
        cwd=source,
        env=env,
        check=True,
    )
    built = list(wheels.glob("*.whl"))
    if len(built) != 1 or not built[0].name.startswith(f"fastuuid-{VERSION}-cp310-abi3-"):
        raise ValueError("expected one fastuuid cp310-abi3 wheel")
    with zipfile.ZipFile(built[0]) as wheel:
        if not any(Path(name).name.upper() == "LICENSE" for name in wheel.namelist()):
            raise ValueError("wheel is missing the upstream license")
    receipt = {
        "source_sha256": SOURCE_SHA256,
        "version": VERSION,
        "maturin": MATURIN_VERSION,
        "build_python": platform.python_version(),
        "wheel": built[0].name,
        "wheel_sha256": hashlib.sha256(built[0].read_bytes()).hexdigest(),
    }
    (wheels / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(built[0])


if __name__ == "__main__":
    main()
