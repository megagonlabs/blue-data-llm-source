#!/usr/bin/env bash
set -euo pipefail
MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The library installer also installs the sibling Blue dependency.
"${MCP_ROOT}/../../lib/scripts/install.sh"

python -m pip install -e "${MCP_ROOT}[dev]"
echo "Done. Run: blue-data-llm-mcp"
