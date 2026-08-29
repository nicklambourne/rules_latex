#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

check_fixture() {
    local fixture="$1"
    local expected="$2"
    local actual

    cd "$ROOT/tests/consumers/$fixture"
    bazel build //:consumer_python @rules_latex//tools:tectonic_compile
    actual="$(bazel run //:consumer_python 2>/dev/null)"
    if [[ "$actual" != "$expected" ]]; then
        echo "ERROR: $fixture used Python $actual; expected $expected" >&2
        return 1
    fi
    bazel run @rules_latex//tools:tectonic_compile -- --help >/dev/null
}

check_fixture python_311 3.11
check_fixture python_314 3.14
