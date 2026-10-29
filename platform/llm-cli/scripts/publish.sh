#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 || "$1" != https://?* ]]; then
    echo "Usage: $0 https://PACKAGE-INDEX-URL" >&2
    exit 2
fi
authority=${1#https://}
authority=${authority%%/*}
if [[ "$authority" == *@* ]]; then
    echo "Repository URL must not contain credentials." >&2
    exit 2
fi
cd "$(dirname "$0")/.."
name=$(sed -n 's/^name = "\([^"]*\)"$/\1/p' pyproject.toml)
version=$(sed -n 's/^version = "\([^"]*\)"$/\1/p' pyproject.toml)
if [[ -z "$name" || -z "$version" ]]; then
    echo "Cannot read package name and version from pyproject.toml." >&2
    exit 1
fi

stem="${name//-/_}-${version}"
wheel="dist/${stem}-py3-none-any.whl"
sdist="dist/${stem}.tar.gz"
shopt -s nullglob
artifacts=(dist/*)
if [[ ! -f "$wheel" || ! -f "$sdist" || ${#artifacts[@]} -ne 2 ]]; then
    echo "Expected only the current version's wheel and sdist in dist/: $stem" >&2
    exit 1
fi

twine check "$wheel" "$sdist"
twine upload --repository-url "$1" "$wheel" "$sdist"
