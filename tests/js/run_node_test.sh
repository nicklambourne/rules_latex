#!/usr/bin/env bash
# Run a JS test file with node's built-in test runner.
#
# Argument: the test file path under the workspace root, e.g.
#   tests/js/serve_web_synctex.test.mjs.
#
# No third-party deps: node --test and node:assert are both built in.
# The tests work with whatever node the runner has installed and need no npm
# install, node_modules, or Bazel JS ruleset. See DESIGN.md §5 #11.

set -euo pipefail

test_path="$1"

# Locate node. Bazel's test sandbox can strip PATH (Bazel 9+ is
# stricter than 8.x), so command -v alone isn't reliable across the CI
# matrix the way /usr/bin/python3 is for the Python tests. Honor an
# explicit $NODE, then PATH, then the usual install locations.
find_node() {
    if [ -n "${NODE:-}" ] && [ -x "${NODE}" ]; then
        printf '%s\n' "${NODE}"
        return 0
    fi
    if command -v node > /dev/null 2>&1; then
        command -v node
        return 0
    fi
    for cand in \
        /opt/homebrew/bin/node \
        /usr/local/bin/node \
        /usr/bin/node; do
        if [ -x "${cand}" ]; then
            printf '%s\n' "${cand}"
            return 0
        fi
    done
    return 1
}

if ! node_bin="$(find_node)"; then
    echo "ERROR: node not found (set \$NODE or put node on PATH)" >&2
    exit 2
fi

echo "Running ${test_path} with $("${node_bin}" --version)..."
exec "${node_bin}" --test "${test_path}"
