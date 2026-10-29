#!/usr/bin/env bash
set -euo pipefail
CLI_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The library installer also installs the sibling Blue dependency.
"${CLI_ROOT}/../../lib/scripts/install.sh"
python -m pip install -e "${CLI_ROOT}[dev]"
echo "Done. Run: blue-llmdb metadata inspect"
