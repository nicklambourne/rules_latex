"""Pinned biber release artifacts.

Biber is the bibliography processor that LaTeX documents using the
`biblatex` package rely on. Tectonic shells out to biber as an
external tool — we provide the binary as a Bazel-resolved toolchain
attribute so users don't have to maintain a system biber install or
worry about CI parity.

## Why biber 2.17 instead of 2.21 (latest)?

Biber and biblatex are tightly version-coupled through a "control
file format" number. The pinned tectonic bundle (`tlextras-2022.0r0`)
ships a build of biblatex 3.17 that produces control file v3.8.
Biber 2.17 reads v3.8; biber 2.18+ require v3.9 or newer. Until the
upstream tlextras bundle is refreshed (the project was archived in
October 2024), biber must stay paired with the v3.8 reader, i.e.
biber 2.17. See DESIGN.md §4.10.

## Mirror

The upstream `biblatex-biber` project distributes prebuilt binaries
via SourceForge. SourceForge only serves a predictable URL scheme
for the `current` release (not version-pinned paths), which makes
content-addressed pinning across upstream bumps fragile. We mirror
the tarballs to a GitHub release on rules_latex
(`biber-mirror-v<version>`) so the URLs are stable and the SHAs
match indefinitely.
"""

BIBER_VERSION = "2.17"
BIBER_MIRROR_TAG = "biber-mirror-v" + BIBER_VERSION

# Modern biber, paired with biblatex 3.21. Opt-in via
# `tectonic.toolchain(modern_biblatex = True)`; bypasses the
# bundle's pinned biblatex 3.17 / biber 2.17 stack so modern
# CTAN extension styles (biblatex-apa 9.x etc.) work. See
# DESIGN.md §4.10 + §5 item #12.
BIBER_MODERN_VERSION = "2.21"
BIBER_MODERN_MIRROR_TAG = "biber-mirror-v" + BIBER_MODERN_VERSION

# Map (os, cpu) -> (asset_name, sha256). The macOS asset is a
# universal binary that works on both Intel and Apple Silicon so it
# covers both cpu entries.
#
# Linux arm64 is not present because upstream doesn't ship a prebuilt
# binary. Documents that need biber on linux/aarch64 must either use
# the `biber_strategy = "system"` escape hatch (less hermetic) or be
# built on a different platform until v0.3 adds biber-from-source for
# that triple. See DESIGN.md §4.9.
BIBER_RELEASES = {
    ("linux", "x86_64"): struct(
        asset = "biber-linux_x86_64.tar.gz",
        sha256 = "129d2e0332a57e985ffa253e5e9fbd28ef99af5a068d1b141145211969aa8999",
        exe = "biber",
    ),
    ("macos", "x86_64"): struct(
        asset = "biber-darwin_universal.tar.gz",
        sha256 = "182e1efa074d8a2a23a8893f2a22440d4e463cce55e4ed02076ac4c0ee0614b2",
        exe = "biber",
    ),
    ("macos", "aarch64"): struct(
        asset = "biber-darwin_universal.tar.gz",
        sha256 = "182e1efa074d8a2a23a8893f2a22440d4e463cce55e4ed02076ac4c0ee0614b2",
        exe = "biber",
    ),
    ("windows", "x86_64"): struct(
        asset = "biber-MSWIN64.zip",
        sha256 = "c103bffc5ae0a7f513e7c26b6d394e9be6cf41952959c5d604ee2e6581b5dea2",
        exe = "biber.exe",
    ),
}

# Same shape as BIBER_RELEASES but for biber 2.21. SHAs are from the
# biber-mirror-v2.21 GitHub release.
BIBER_MODERN_RELEASES = {
    ("linux", "x86_64"): struct(
        asset = "biber-linux_x86_64.tar.gz",
        sha256 = "f00dfa29c7f798695339d9155abefcc0da4bd2fb1b4b2d90e46693f261b0a26e",
        exe = "biber",
    ),
    ("macos", "x86_64"): struct(
        asset = "biber-darwin_universal.tar.gz",
        sha256 = "8c895defed5e69b7a824cb7b7947e8bbfa3f3b17ffb8a1d493e982b679e6633c",
        exe = "biber",
    ),
    ("macos", "aarch64"): struct(
        asset = "biber-darwin_universal.tar.gz",
        sha256 = "8c895defed5e69b7a824cb7b7947e8bbfa3f3b17ffb8a1d493e982b679e6633c",
        exe = "biber",
    ),
    ("windows", "x86_64"): struct(
        asset = "biber-MSWIN64.zip",
        sha256 = "2ae8323193db40f87f6471b3c9a8378059bf73a37ce15189f788770e6d93e353",
        exe = "biber.exe",
    ),
}

def biber_download_url(asset, mirror_tag = None):
    """Build the GitHub release URL for a mirrored biber asset."""
    return "https://github.com/nicklambourne/rules_latex/releases/download/{tag}/{asset}".format(
        tag = mirror_tag or BIBER_MIRROR_TAG,
        asset = asset,
    )
