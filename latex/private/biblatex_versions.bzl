"""Pinned biblatex package artifact for the modern-biblatex opt-in.

The default rules_latex toolchain uses biblatex 3.17 from Tectonic's
2022 bundle. When a workspace opts in via
`tectonic.toolchain(modern_biblatex = True)`, we fetch a newer
biblatex from CTAN and overlay it via `-Z search-path`, so modern
extension styles (`biblatex-apa` 9.x, `biblatex-chicago`,
`biblatex-ieee`, etc.) work correctly. This must be paired with
biber 2.21 (see biber_versions.bzl `BIBER_MODERN_RELEASES`); the
biblatex/biber `.bcf` control-file format is version-coupled.

## Source

We pull directly from CTAN's canonical mirror:

    https://mirrors.ctan.org/macros/latex/contrib/biblatex.zip

CTAN's URL serves the latest published biblatex. We pin a content
hash so the build is deterministic across re-fetches; if CTAN
publishes a new biblatex, the SHA mismatch will fail the build
loudly and the maintainer bumps the pin here.

Refresh procedure when bumping:

    curl -sSL -o /tmp/biblatex.zip https://mirrors.ctan.org/macros/latex/contrib/biblatex.zip
    shasum -a 256 /tmp/biblatex.zip
    unzip -p /tmp/biblatex.zip biblatex/latex/biblatex.sty | grep abx@version

Update BIBLATEX_VERSION + BIBLATEX_SHA256 below. Also verify that
the pinned biber version (biber_versions.bzl `BIBER_MODERN_VERSION`)
still matches biblatex's expected biber via biblatex's INSTALL or
release notes.
"""

# biblatex version shipped at this SHA. Updated together with
# BIBLATEX_SHA256 below. Informational; not used at runtime.
BIBLATEX_VERSION = "3.21"

# CTAN serves the package zip at this stable URL; the SHA pin makes
# the fetch deterministic.
BIBLATEX_URL = "https://mirrors.ctan.org/macros/latex/contrib/biblatex.zip"
BIBLATEX_SHA256 = "b0394247a9d1f7dca29bf5e838cfac2b506f77abe9e13f9c614839dec97da41d"

# Inside the zip the package lives under a top-level `biblatex/`
# directory; the LaTeX-side files (`.sty`/`.bbx`/`.cbx`/`.lbx`/
# `.dbx`/`.def`/`.cfg`) live under `biblatex/latex/`. The toolchain
# exposes the unpacked `biblatex/latex/` tree to consumers, which
# walk it for `-Z search-path` entries.
BIBLATEX_STRIP_PREFIX = "biblatex"
