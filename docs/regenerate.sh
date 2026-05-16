#!/usr/bin/env bash
# Copy Stardoc-generated Markdown back into the source tree.
#
# Invoked by `bazel run //docs:regenerate`. Args are the page names
# (e.g. `rules providers toolchain`); the script locates each one
# under bazel-bin and copies it into docs/api/<name>.md in the
# workspace root.

set -euo pipefail

if [[ -z "${BUILD_WORKSPACE_DIRECTORY:-}" ]]; then
    echo "ERROR: run via 'bazel run //docs:regenerate', not 'bazel build'." >&2
    exit 1
fi

dest_dir="${BUILD_WORKSPACE_DIRECTORY}/docs/site/api"
mkdir -p "${dest_dir}"

for name in "$@"; do
    # The stardoc-generated files live under the runfiles tree at
    # <package>/api/<name>.md. `bazel run` sets pwd to the runfiles
    # root, so this is just a relative path.
    src="docs/api/${name}.md"
    if [[ ! -f "${src}" ]]; then
        echo "ERROR: expected generated file at ${src} not found in runfiles" >&2
        exit 1
    fi
    # Make the destination writable in case a previous bazel build
    # left it as a read-only output-tree copy.
    [[ -f "${dest_dir}/${name}.md" ]] && chmod u+w "${dest_dir}/${name}.md"
    cp "${src}" "${dest_dir}/${name}.md"
    echo "  wrote ${dest_dir}/${name}.md"
done

echo
echo "Done. Review with: git diff docs/site/api/"
