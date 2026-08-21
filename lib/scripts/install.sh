#!/usr/bin/env bash
set -euo pipefail
LIB_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# blue-data is a core dependency and is not published to a public index, so it is installed
# from its checkout first. Override BLUE_DATA_PATH if yours lives elsewhere. Without this a
# clean environment fails to resolve it, which reads as a broken package rather than a
# missing sibling.
BLUE_DATA_PATH="${BLUE_DATA_PATH:-${LIB_ROOT}/../../blue-data/lib}"
if [ -d "${BLUE_DATA_PATH}" ]; then
    pip install -e "${BLUE_DATA_PATH}"
else
    echo "blue-data not found at ${BLUE_DATA_PATH}." >&2
    echo "Set BLUE_DATA_PATH to its lib directory, or install it from your internal index." >&2
    exit 1
fi

pip install -e "${LIB_ROOT}[dev,llm]"
echo "Done. Run: python -c 'import blue_data_llm_source'"
