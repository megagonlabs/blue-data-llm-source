#!/usr/bin/env bash
set -euo pipefail
CLI_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Through the library's own installer, which puts blue-data in place first. blue-data is a
# core dependency and is not on a public index, so installing the library directly fails to
# resolve it and reads as a broken package rather than a missing sibling.
"${CLI_ROOT}/../../lib/scripts/install.sh"
pip install -e "${CLI_ROOT}[dev]"
echo "Done. Run: llm info"
