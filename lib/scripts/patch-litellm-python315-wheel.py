#!/usr/bin/env python3
"""Prepare the LiteLLM 1.106.5 compatibility wheel for regular CPython 3.15."""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import hashlib
import io
import zipfile
from pathlib import Path, PurePosixPath

VERSION = "1.106.5+blue.1"
UPSTREAM_SHA256 = {
    "macosx_10_12_x86_64": "5c9f62dc04eb5a60a9f60a1faaa509a01432661b1638b1f34266197063b4d2d7",
    "macosx_11_0_arm64": "25d71b305f7d0c67b990f21993f4aa9cd62c5a48ca0400b9b7aba6a355e2ad0b",
    "manylinux_2_28_aarch64": "80f8b7cd0e5e66ab8011e1391eb2f9749524539ac66aa85dc93c68df038ef9eb",
    "manylinux_2_28_x86_64": "9c8ebd30c4a5643207b8e9dade2680c540bbe17ca9543474d12147f6cd2f913f",
    "musllinux_1_2_aarch64": "a4d2dedfe16a39659b9483afd3ce13c2170f73ab53ee60a1f9a6e5604a9e5f8f",
    "musllinux_1_2_x86_64": "dd15a08b86858b7f83b78685413f4cfb47c2713b4964b804a11208c937a98009",
    "win_amd64": "daf357933069999b3360b84982391cf72a62869bae2fddb644b4ee82a1324a0d",
}
CONFIG = "litellm/router_strategy/complexity_router/config.py"
OLD_IMPORTS = b"    import sre_constants\n    import sre_parse\n"
NEW_IMPORTS = (
    b"    from re import _constants as sre_constants\n    from re import _parser as sre_parse\n"
)


def transform(source: Path, destination: Path) -> Path:
    platform = source.name.removeprefix("litellm-1.106.5-cp310-abi3-").removesuffix(".whl")
    expected = UPSTREAM_SHA256.get(platform)
    if (
        source.name != f"litellm-1.106.5-cp310-abi3-{platform}.whl"
        or expected is None
        or hashlib.sha256(source.read_bytes()).hexdigest() != expected
    ):
        raise ValueError("expected a checksum-verified official LiteLLM 1.106.5 wheel")
    target = destination / source.name.replace("-1.106.5-", f"-{VERSION}-", 1)
    old_info = "litellm-1.106.5.dist-info/"
    new_info = f"litellm-{VERSION}.dist-info/"
    with zipfile.ZipFile(source) as original:
        names = original.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate wheel entries")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("unsafe wheel entry")
            if name.endswith(("/RECORD.jws", "/RECORD.p7s")):
                raise ValueError("cannot modify a signed wheel")
        if original.read(CONFIG).count(OLD_IMPORTS) != 1:
            raise ValueError("unexpected LiteLLM regex imports")
        metadata = original.read(old_info + "METADATA")
        if metadata.count(b"\nVersion: 1.106.5\n") != 1:
            raise ValueError("unexpected LiteLLM metadata version")
        if metadata.count(b"\nRequires-Python: >=3.10, <3.15\n") != 1:
            raise ValueError("unexpected LiteLLM Python requirement")
        destination.mkdir(parents=True, exist_ok=True)
        records: list[list[str]] = []
        with target.open("xb") as output, zipfile.ZipFile(output, "w") as patched:
            for info in original.infolist():
                if info.filename == old_info + "RECORD":
                    continue
                data = original.read(info.filename)
                if info.filename == CONFIG:
                    data = data.replace(OLD_IMPORTS, NEW_IMPORTS, 1)
                elif info.filename == old_info + "METADATA":
                    data = data.replace(
                        b"\nVersion: 1.106.5\n", f"\nVersion: {VERSION}\n".encode(), 1
                    )
                    # 3.15 only: the patched imports fail on 3.10, and 3.11-3.14 should keep
                    # the upstream wheel even when this wheelhouse is visible to pip.
                    data = data.replace(
                        b"\nRequires-Python: >=3.10, <3.15\n",
                        b"\nRequires-Python: >=3.15, <3.16\n",
                        1,
                    )
                entry = copy.copy(info)
                if entry.filename.startswith(old_info):
                    entry.filename = new_info + entry.filename[len(old_info) :]
                patched.writestr(entry, data)
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
                records.append([entry.filename, "sha256=" + digest.decode(), str(len(data))])
            records.append([new_info + "RECORD", "", ""])
            text = io.StringIO(newline="")
            csv.writer(text, lineterminator="\n").writerows(records)
            entry = copy.copy(original.getinfo(old_info + "RECORD"))
            entry.filename = new_info + "RECORD"
            patched.writestr(entry, text.getvalue())
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="Official upstream wheel for your platform")
    parser.add_argument("--wheel-dir", type=Path, required=True, help="Output wheelhouse")
    args = parser.parse_args()
    print(transform(args.wheel.resolve(), args.wheel_dir.resolve()))


if __name__ == "__main__":
    main()
