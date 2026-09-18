#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
twine upload dist/*
