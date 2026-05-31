"""Pinned biber release artifacts.

Biber is the bibliography processor that LaTeX documents using the
`biblatex` package rely on. Tectonic shells out to biber as an
external tool — we provide the binary as a Bazel-resolved toolchain
attribute so users don't have to maintain a system biber install or
worry about CI parity.

## Why biber 2.21

Biber and biblatex are tightly version-coupled through a "control
file format" number: biblatex writes a `.bcf` control file in the
format it knows and biber refuses one whose format it doesn't
recognise. The pinned package bundle (`texlive2026`, hosted on R2 —
see `bundles.bzl`) ships biblatex 3.21, which writes control file
v3.11; biber 2.21 is the matching reader. (Historically rules_latex
pinned biber 2.17 to match the frozen 2022 bundle's biblatex 3.17 /
v3.8, with an opt-in `modern_biblatex` overlay for 3.21; the TL2026
bundle ships 3.21 natively, so the split — and the old 2.17 pin — is
gone. See DESIGN.md §4.10.)

## Mirror

The upstream `biblatex-biber` project distributes prebuilt binaries
via SourceForge (no version-pinned URLs) plus, for linux/aarch64,
CTAN's `biber-linux-aarch64` package. We mirror the tarballs to a
GitHub release on rules_latex (`biber-mirror-v<version>`) so the URLs
are stable and the SHAs match indefinitely.
"""

BIBER_VERSION = "2.21"
BIBER_MIRROR_TAG = "biber-mirror-v" + BIBER_VERSION

# Map (os, cpu) -> (asset_name, sha256, exe). The macOS asset is a
# universal binary that covers both Intel and Apple Silicon. The
# linux/aarch64 binary is the prebuilt biber 2.21 from CTAN's
# `biber-linux-aarch64` package (re-gzipped to the standard layout);
# CI-verified on the ubuntu-24.04-arm runner. SHAs are from the
# biber-mirror-v2.21 GitHub release.
BIBER_RELEASES = {
    ("linux", "x86_64"): struct(
        asset = "biber-linux_x86_64.tar.gz",
        sha256 = "f00dfa29c7f798695339d9155abefcc0da4bd2fb1b4b2d90e46693f261b0a26e",
        exe = "biber",
    ),
    ("linux", "aarch64"): struct(
        asset = "biber-linux_aarch64.tar.gz",
        sha256 = "515a3ffed550a6e71e69c712bff338ce261122de4af2bdb650805d9e23c95c0c",
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
