#!/usr/bin/env bash
# Run a Python unittest module by relative path.
#
# Argument: the test file path under the workspace root, e.g.
#   tests/py/test_synctex_parser.py.
#
# Doesn't depend on rules_python; works on whatever python3 the
# runner has installed. Mirrors how the rules_latex tooling itself
# is invoked.

set -euo pipefail

test_path="$1"

if ! command -v python3 > /dev/null; then
    echo "ERROR: python3 not on PATH" >&2
    exit 2
fi

# Convert the test path to a dotted module name. The test runner
# wants something like `tests.py.test_synctex_parser` not a file
# path.
module="${test_path%.py}"
module="${module//\//.}"

echo "Running ${module}..."
exec python3 -m unittest -v "${module}"
