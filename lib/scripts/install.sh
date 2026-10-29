#!/usr/bin/env bash
set -euo pipefail
LIB_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONSTRAINTS=""

if python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 15))'; then
    if [ -z "${PIP_FIND_LINKS:-}" ] && ! python -c \
        'from importlib.metadata import version; assert version("fastuuid") == "0.14.0+blue.1" and version("litellm") == "1.106.5+blue.1"' \
        >/dev/null 2>&1; then
        echo "Python 3.15 needs the provider compatibility wheels. Build them and set PIP_FIND_LINKS; see docs/installation.md#python-315-installation." >&2
        exit 1
    fi
    CONSTRAINTS="${LIB_ROOT}/scripts/python315-constraints.txt"
fi

# Install the unpublished Blue dependency from its checkout first.
BLUE_DATA_PATH="${BLUE_DATA_PATH:-${LIB_ROOT}/../../blue-data/lib}"
if [ -f "${BLUE_DATA_PATH}/pyproject.toml" ]; then
    python -m pip install -e "${BLUE_DATA_PATH}"
else
    echo "blue-data pyproject.toml not found at ${BLUE_DATA_PATH}." >&2
    echo "Set BLUE_DATA_PATH to the lib directory of your blue-data checkout; see docs/installation.md." >&2
    exit 1
fi

python -m pip install -e "${LIB_ROOT}[dev,llm]" ${CONSTRAINTS:+-c "${CONSTRAINTS}"}
echo "Done. Run: python -c 'import blue_data_llm_source'"
