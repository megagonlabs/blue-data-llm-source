#!/usr/bin/env bash
set -euo pipefail
LIB_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Install the unpublished Blue dependency from its checkout first.
BLUE_DATA_PATH="${BLUE_DATA_PATH:-${LIB_ROOT}/../../blue-data/lib}"
if [ -f "${BLUE_DATA_PATH}/pyproject.toml" ]; then
    python -m pip install -e "${BLUE_DATA_PATH}"
else
    echo "blue-data pyproject.toml not found at ${BLUE_DATA_PATH}." >&2
    echo "Set BLUE_DATA_PATH to the lib directory of your blue-data checkout; see docs/installation.md." >&2
    exit 1
fi

python -m pip install -e "${LIB_ROOT}[dev,llm]"
echo "Done. Run: python -c 'import blue_data_llm_source'"
