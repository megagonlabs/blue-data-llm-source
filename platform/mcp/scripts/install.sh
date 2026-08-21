#!/usr/bin/env bash
set -euo pipefail
MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Through the library's own installer, which puts blue-data in place first. blue-data is a
# core dependency of the library and is not on a public index, so installing this package
# directly fails to resolve it and reads as a broken package rather than a missing sibling.
"${MCP_ROOT}/../../lib/scripts/install.sh"

pip install -e "${MCP_ROOT}[dev]"
echo "Done. Run: blue-data-llm-mcp"
