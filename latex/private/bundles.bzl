"""Pinned tectonic package bundles.

Tectonic resolves `\\usepackage{...}` directives at compile time by fetching
files from a "package bundle" — a single tar archive that mirrors a subset of
TeX Live. By default tectonic downloads the bundle on first run from
`relay.fullyjustified.net`, which is convenient but non-hermetic.

`rules_latex` ships an optional pinned bundle that consumers can opt into for
fully offline, content-addressed compilation. To use it, add a
`tectonic.bundle()` tag to the `tectonic` module extension call in your
`MODULE.bazel`. See [`DESIGN.md` §4.4](../../DESIGN.md) for the network policy.

To update: pick a new upstream release from
https://github.com/tectonic-typesetting/tectonic-texlive-bundles/releases,
fetch its `.sha256sum` companion file, and bump the constants below.
"""

# The canonical tlextras bundle that ships with the upstream
# `tectonic-texlive-bundles` project. The tar itself is hosted on Tectonic's
# CDN at data1.fullyjustified.net; the sha256 is published alongside the
# upstream GitHub release as a small companion file.
DEFAULT_BUNDLE = struct(
    version = "2021.3r1",
    url = "https://data1.fullyjustified.net/tlextras-2021.3r1.tar",
    sha256 = "264dfa0c090c395eb18859e35263a8f12b34678a3e01e2847690a5b3518f8360",
)
