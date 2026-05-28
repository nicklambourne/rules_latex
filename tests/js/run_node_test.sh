#!/usr/bin/env bash
# Run a JS test file with node's built-in test runner.
#
# Argument: the test file path under the workspace root, e.g.
#   tests/js/serve_web_synctex.test.mjs.
#
# Mirrors tests/py/run_pytest.sh: no third-party deps (node --test and
# node:assert are both built in, the JS analogue of `python3 -m
# unittest`), works on whatever node the runner has installed. Keeps the
# repo's stdlib-only-tooling convention — no npm install, no node_modules,
# no Bazel JS ruleset. See DESIGN.md §5 #11.

set -euo pipefail

test_path="$1"

if ! command -v node > /dev/null; then
    echo "ERROR: node not on PATH" >&2
    exit 2
fi

echo "Running ${test_path} with node $(node --version)..."
exec node --test "${test_path}"
