"""Pinned tectonic package bundles.

Tectonic resolves `\\usepackage{...}` directives at compile time by fetching
files from a "package bundle" — a single tar archive that mirrors a subset of
TeX Live. By default tectonic downloads the bundle on first run from
`relay.fullyjustified.net`, which is convenient but non-hermetic.

`rules_latex` ships an optional pinned bundle that consumers can opt into for
fully offline, content-addressed compilation. To use it, add a
`tectonic.bundle()` tag to the `tectonic` module extension call in your
`MODULE.bazel`. See [`DESIGN.md` §4.4](../../DESIGN.md) for the network policy.

The pinned bundle MUST match the version Tectonic itself expects (it asks
the relay for `default_bundle_v<N>.tar` where N is encoded in the binary).
Tectonic 0.16.9 expects v33, which the relay resolves to
`tlextras-2022.0r0.tar`. Mismatched bundles produce confusing
"requested file not found in bundle" errors at compile time.

Note that the upstream `tectonic-texlive-bundles` repo (which historically
published the matching `.sha256sum` companion file) was archived in October
2024 with `tlextras-2021.3r1` as its only tagged release. The 2022.0r0
bundle is unreleased on GitHub but the asset on `data1.fullyjustified.net`
is content-stable (same Last-Modified and ETag since 2022-09-25). The
sha256 below was computed locally from a fresh download; update it the
same way if you ever bump the version.

To update: pick a new bundle URL, fetch it, run `sha256sum` on the result,
and bump the constants below.
"""

# The canonical tlextras bundle that ships with tectonic 0.16.9 (bundle
# format v33). Hosted on Tectonic's CDN; the hash was computed locally
# from a fresh download because upstream stopped publishing companion
# sha256 files after 2021.3r1.
DEFAULT_BUNDLE = struct(
    version = "2022.0r0",
    url = "https://data1.fullyjustified.net/tlextras-2022.0r0.tar",
    sha256 = "425685e124746c15ba9bb8e0596bdaad98fce886afa347fbcf9ec0e9acd7fe79",
)
