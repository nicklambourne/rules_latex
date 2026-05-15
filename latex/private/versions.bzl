"""Pinned tectonic release artifacts.

To update: bump TECTONIC_VERSION, regenerate the SHA256 hashes by downloading
each artifact and running `sha256sum`, and update this file. The integrity
hashes are baked in so that `bazel build` is reproducible and offline once
the repository cache is warm.
"""

TECTONIC_VERSION = "0.16.9"

# Map from (os, cpu) to (asset_name, sha256).
#
# Hashes were obtained from the GitHub release at
# https://github.com/tectonic-typesetting/tectonic/releases/tag/tectonic%400.16.9
TECTONIC_RELEASES = {
    # Both linux variants use the statically-linked musl artifact so the
    # binary runs on glibc-based distributions as old as the supported
    # Bazel runtime requires (the upstream `*-linux-gnu` build links
    # against a very recent glibc and does not run on, e.g., Ubuntu
    # 22.04).
    ("linux", "x86_64"): struct(
        asset = "tectonic-{version}-x86_64-unknown-linux-musl.tar.gz",
        sha256 = "60b13a0826ae7ad9ce34b4a2df06bff2cfcfa6dda8a915477c0cbb84e1a4a902",
    ),
    ("linux", "aarch64"): struct(
        asset = "tectonic-{version}-aarch64-unknown-linux-musl.tar.gz",
        sha256 = "f9aa39017dbd51f111fdb93dda222178cbe51c8193508fc567b523cc74fff9c1",
    ),
    ("macos", "x86_64"): struct(
        asset = "tectonic-{version}-x86_64-apple-darwin.tar.gz",
        sha256 = "79d8839fa3594bfea9b2bf2ac0a0455bcc4d0de956a5e5c403107e9a72f79e86",
    ),
    ("macos", "aarch64"): struct(
        asset = "tectonic-{version}-aarch64-apple-darwin.tar.gz",
        sha256 = "edb67c61aba768289f6da441c9e6f523cfaff4f8b2a5708523ef29c543f8e88e",
    ),
}

def tectonic_download_url(asset, version = TECTONIC_VERSION):
    """Build the GitHub release download URL for a tectonic asset."""
    return "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{version}/{asset}".format(
        version = version,
        asset = asset.format(version = version),
    )
