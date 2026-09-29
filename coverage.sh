#!/bin/bash
set -euo pipefail

# generates coverage report from unit tests

echo "==> Cleaning previous coverage data..."
rm -f .coverage .coverage.* coverage.xml
rm -rf coverage_report/

echo "==> Running unit tests with coverage..."
pytest test/ --cov=src --cov-report=html --cov-report=xml:coverage.xml -v

echo
echo "==> Opening report..."
# detects OS and uses appropriate command
os_name=$(uname -s)
if [[ "$os_name" == "Darwin" ]]; then
    open coverage_report/index.html || echo "Could not open browser. Open coverage_report/index.html manually." >&2
elif [[ "$os_name" == "Linux" ]]; then
    xdg-open coverage_report/index.html 2>/dev/null || echo "Could not open browser. Open coverage_report/index.html manually." >&2
else
    echo >&2 "Unknown OS. Please open coverage_report/index.html manually."
fi

echo
echo "Done! HTML report available at: coverage_report/index.html"
