"""Pinned tectonic package bundle.

Tectonic resolves `\\usepackage{...}` directives at compile time by fetching
files from a "package bundle" that mirrors a subset of TeX Live.

`rules_latex` pins its own bundle: **`texlive2026`**, a freshly-built
TeX Live 2026 bundle in the `.ttb` (formatspec-v1) format — per-file gzip
plus an internal offset index, so it's HTTP-range-addressable (tectonic
fetches only the files a document needs). We host it on Cloudflare R2 at
`rules-latex.ndl.au` (zero egress, durable), with the `.ttb.index.gz`
sidecar alongside for the web-fetch path. The pinned tectonic 0.16.9
reads this `.ttb` directly (verified in CI).

Why we host our own: upstream `tectonic-texlive-bundles` was archived in
October 2024 with no bundle newer than 2022, and tectonic's relay still
serves the frozen `tlextras-2022.0r0` (biblatex 3.17). Building + hosting
TL2026 ourselves keeps packages current and removes the third-party CDN
dependency. See `DESIGN.md` §4.4 (network policy) and §4.10 (the
bundle-staleness rationale and the rebuild procedure).

A root module can still repoint the full-bundle download at a different
mirror via `tectonic.bundle(url = ..., sha256 = ...)` — see `DESIGN.md`
§4.4 "Self-hosting the bundle".

To refresh: rebuild the bundle (DESIGN.md §4.10), upload the new
`.ttb` + `.ttb.index.gz` to R2, and bump `url`/`sha256`/`version` below.
"""

# rules_latex's TeX Live 2026 bundle, built via the tectonic-texlive-bundles
# Rust builder and hosted on Cloudflare R2. `.ttb` formatspec-v1; consumed
# by tectonic 0.16.9 (local `--bundle` or the range-fetched web URL). The
# matching bibliography stack is biblatex 3.21 / biber 2.21 (biber_versions.bzl).
DEFAULT_BUNDLE = struct(
    version = "texlive2026",
    url = "https://rules-latex.ndl.au/texlive2026.ttb",
    sha256 = "e1778ceb8a2f5cc6196d476d076592bc946f3319faf7101fcd957f8580e62b80",
    format = "ttb",
)
