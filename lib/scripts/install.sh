#!/usr/bin/env bash
set -euo pipefail
LIB_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# blue-data is a core dependency and is not published to a public index, so install it from
# its checkout first. Set BLUE_DATA_PATH if the checkout is elsewhere. Otherwise, a clean
# environment cannot resolve the dependency.
BLUE_DATA_PATH="${BLUE_DATA_PATH:-${LIB_ROOT}/../../blue-data/lib}"
if [ -d "${BLUE_DATA_PATH}" ]; then
    pip install -e "${BLUE_DATA_PATH}"
else
    echo "blue-data not found at ${BLUE_DATA_PATH}." >&2
    echo "Set BLUE_DATA_PATH to the lib directory of your blue-data checkout; see docs/installation.md." >&2
    exit 1
fi

pip install -e "${LIB_ROOT}[dev,llm]"
echo "Done. Run: python -c 'import blue_data_llm_source'"
