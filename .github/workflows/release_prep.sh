#!/usr/bin/env bash
# Release-preparation script invoked by
# bazel-contrib/.github/.github/workflows/release_ruleset.yaml.
#
# Responsibilities:
#   1. Validate the tag matches MODULE.bazel's version field.
#   2. Produce the source archive (rules_latex-X.Y.Z.tar.gz).
#   3. Produce the BCR-Stardoc docs archive (rules_latex-X.Y.Z.docs.tar.gz)
#      containing every starlark_doc_extract binaryproto in the build
#      graph. The BCR's docs renderer picks this up via the
#      docs_url field in .bcr/source.template.json.
#   4. Print the release notes preamble to stdout. The reusable
#      workflow concatenates this with GitHub-generated changelog
#      content.

set -euo pipefail

TAG="${1:?usage: release_prep.sh <tag>}"
VERSION="${TAG#v}"
ARCHIVE="rules_latex-${VERSION}.tar.gz"
DOCS_ARCHIVE="rules_latex-${VERSION}.docs.tar.gz"

# --- 1. Validate tag vs MODULE.bazel ------------------------------------------

mod_version="$(grep -oE 'version = "[^"]+"' MODULE.bazel | head -1 | sed -E 's/version = "(.+)"/\1/')"
if [[ "${mod_version}" != "${VERSION}" ]]; then
    echo "FAIL: MODULE.bazel version (${mod_version}) does not match tag (${VERSION})." >&2
    exit 1
fi

# --- 2. Source archive --------------------------------------------------------

# Use `git archive` to get a deterministic snapshot at the tagged commit,
# respecting .gitattributes for export-ignore. The prefix makes
# `tar -xzf` land directly in a versioned directory.
git archive --format=tar.gz \
    --prefix="rules_latex-${VERSION}/" \
    -o "${ARCHIVE}" \
    "${TAG}"
SHA256="$(sha256sum "${ARCHIVE}" | cut -d' ' -f1)"

# Pre-formatted "sha256-..." integrity hint for the BCR source.json,
# in case a human is reading these notes and assembling the BCR PR
# by hand.
INTEGRITY="sha256-$(printf '%s' "${SHA256}" | xxd -r -p | base64 -w0)"

# --- 3. Docs archive ----------------------------------------------------------
#
# Build every starlark_doc_extract target in the workspace and bundle
# the resulting binaryproto files. The BCR consumes these to render
# Stardoc-style API reference pages on registry.bazel.build (see
# https://blog.aspect.build/stardocs-on-bcr). Per
# https://github.com/bazelbuild/bazel-central-registry/blob/main/docs/stardoc.md
# we build into a dedicated output_base to keep the archive free of
# unrelated build state.

DOCS_BASE="$(mktemp -d)"
TARGETS_FILE="$(mktemp)"

bazel --output_base="${DOCS_BASE}" query \
    --output=label \
    'kind("starlark_doc_extract rule", //...)' \
    > "${TARGETS_FILE}"
bazel --output_base="${DOCS_BASE}" build --target_pattern_file="${TARGETS_FILE}"

tar --create --auto-compress \
    --directory "$(bazel --output_base="${DOCS_BASE}" info bazel-bin)" \
    --file "${GITHUB_WORKSPACE:-${PWD}}/${DOCS_ARCHIVE}" \
    .

# --- 4. Release notes preamble (stdout) ---------------------------------------

cat <<EOF
## Installation

\`\`\`python
bazel_dep(name = "rules_latex", version = "${VERSION}")
\`\`\`

## Bazel Central Registry submission

The BCR \`source.json\` fields for this release:

\`\`\`json
{
  "url": "https://github.com/${GITHUB_REPOSITORY:-nicklambourne/rules_latex}/releases/download/${TAG}/${ARCHIVE}",
  "integrity": "${INTEGRITY}",
  "strip_prefix": "rules_latex-${VERSION}",
  "docs_url": "https://github.com/${GITHUB_REPOSITORY:-nicklambourne/rules_latex}/releases/download/${TAG}/${DOCS_ARCHIVE}"
}
\`\`\`

Release-archive sha256: \`${SHA256}\`

EOF
