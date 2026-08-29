#!/usr/bin/env python3
"""Generate the bundle-resident package manifest from the tectonic bundle.

The CTAN auto-resolver in ``tectonic_populate_cache.py`` consults this
manifest to decide whether a ``\\RequirePackage`` reference is already
provided by the tectonic bundle. References resolvable from the bundle
are *not* fetched from CTAN, which avoids the version-coupling
disasters that come from shadowing bundle packages (for example, a biblatex
release newer than the bundle's 3.21 fetched over its paired biber 2.21 — see
DESIGN.md §4.10).

This tool is invoked manually by a maintainer (a) when first
generating the manifest and (b) any time tectonic's pinned bundle
version is bumped (see DESIGN.md §5 item #4).

Two enumeration modes:

  * ``--index-url`` / ``--index-file`` (default): parse the ``.ttb``
    bundle's published ``.ttb.index.gz`` file listing. This is the
    source of truth for our self-hosted bundle — ``tectonic -X bundle
    search`` has no flag to target an arbitrary bundle URL, so it can
    only enumerate tectonic's built-in default bundle, not ours.
  * ``--tectonic``: run ``tectonic -X bundle search ''`` against
    tectonic's *default* bundle. Legacy fallback, kept for the rare
    case of regenerating against the built-in bundle.

Output is a sorted, deduplicated list of ``.sty``/``.cls`` basenames
(without extensions), one per line, written to
``latex/toolchain/bundle_manifest.txt`` by default. ~90 KB of
diff-friendly text; ~6,900 entries for the ``texlive2026`` ``.ttb``
bundle.

Usage:
    # From the pinned bundle's index sidecar (default URL):
    python3 tools/extract_bundle_manifest.py [--output <path>]
    # Or an explicit index URL / local file:
    python3 tools/extract_bundle_manifest.py --index-url <url>
    python3 tools/extract_bundle_manifest.py --index-file <path>
    # Legacy: tectonic's built-in default bundle:
    python3 tools/extract_bundle_manifest.py --tectonic <path>
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


# Default index sidecar for the pinned bundle. Must track
# DEFAULT_BUNDLE.url in latex/private/bundles.bzl (the bundle's
# `.ttb.index.gz` sibling). The `.ttb` formatspec publishes a plain-text
# file listing alongside the bundle; parsing it is how we enumerate the
# bundle's packages without a local copy. `tectonic -X bundle search`
# can only enumerate tectonic's *built-in default* bundle (it has no
# flag to target an arbitrary bundle URL), so for our self-hosted .ttb
# the index sidecar is the source of truth.
_DEFAULT_INDEX_URL = "https://rules-latex.ndl.au/texlive2026.ttb.index.gz"


# Match ``.sty`` and ``.cls``. We deliberately do not include ``.def``
# or ``.cfg`` files: ``\RequirePackage{X}`` / ``\usepackage{X}`` look up
# ``X.sty`` specifically. Documents that do ``\input{X.def}`` directly are
# rare in practice and the scanner doesn't pick those up either.
_PACKAGE_FILE_RE = re.compile(r"^(.+)\.(sty|cls)$")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tectonic",
        type=Path,
        default=None,
        help="Legacy mode: path to the tectonic binary. Enumerates "
             "tectonic's *built-in default* bundle via `-X bundle "
             "search` (it cannot target our self-hosted .ttb). The "
             "toolchain-resolved tectonic from a populated bazel "
             "workspace lives under $(bazel info output_base)/external/"
             "+tectonic+rules_latex_tectonic_*. Mutually exclusive with "
             "--index-url/--index-file.",
    )
    p.add_argument(
        "--index-url",
        default=None,
        help="URL of the bundle's .ttb.index.gz file listing. "
             "Defaults to the pinned bundle's index "
             f"({_DEFAULT_INDEX_URL}) when no enumeration mode is "
             "given. Mutually exclusive with --tectonic/--index-file.",
    )
    p.add_argument(
        "--index-file",
        type=Path,
        default=None,
        help="Local path to a (gzipped) .ttb.index.gz file listing, "
             "as an alternative to fetching --index-url. Mutually "
             "exclusive with --tectonic/--index-url.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("latex/toolchain/bundle_manifest.txt"),
        help="Where to write the manifest. Default is the canonical "
             "path under latex/toolchain/.",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="TECTONIC_CACHE_DIR override. Defaults to a fresh "
             "tempdir so we don't pollute the user's real cache.",
    )
    return p.parse_args()


def enumerate_bundle(tectonic: Path, cache_dir: Path) -> list[str]:
    """Run ``tectonic -X bundle search ''`` and return every entry."""
    env = os.environ.copy()
    env["TECTONIC_CACHE_DIR"] = str(cache_dir.resolve())
    # Search with an empty term to list every file in the bundle. The
    # first invocation downloads the bundle (~50 MB) into cache_dir;
    # subsequent invocations reuse it.
    print(
        f"Enumerating tectonic bundle (cache: {cache_dir})...",
        file=sys.stderr,
    )
    result = subprocess.run(
        [str(tectonic), "-X", "bundle", "search", ""],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def enumerate_bundle_from_index(index_lines: list[str]) -> list[str]:
    """Return the file paths listed under an .ttb index's [FILELIST].

    The ``.ttb`` formatspec-v1 index is a plain-text file with sections:
    ``[DEFAULTSEARCH]``, ``[SEARCH:<name>]`` (search-path globs), and
    ``[FILELIST]`` — under which each line is
    ``<mtime> <offset> <length> <sha256> <path>``. We return the final
    whitespace-separated field (the path) of every [FILELIST] entry.
    """
    paths: list[str] = []
    in_filelist = False
    for raw in index_lines:
        line = raw.rstrip("\n")
        if line.startswith("["):
            in_filelist = line == "[FILELIST]"
            continue
        if not in_filelist or not line:
            continue
        # `<mtime> <offset> <length> <sha256> <path>`; path is the last
        # field and TeX bundle paths never contain spaces.
        parts = line.split()
        if len(parts) >= 5:
            paths.append(parts[-1])
    return paths


def _read_index(index_url: str | None, index_file: Path | None) -> list[str]:
    """Fetch/read and gunzip an .ttb.index.gz, returning its text lines."""
    if index_file is not None:
        print(f"Reading bundle index from {index_file}...", file=sys.stderr)
        data = index_file.read_bytes()
    else:
        assert index_url is not None
        print(f"Fetching bundle index from {index_url}...", file=sys.stderr)
        with urllib.request.urlopen(index_url) as resp:
            data = resp.read()
    # The sidecar is gzip-compressed; tolerate an already-plain file too.
    try:
        text = gzip.decompress(data).decode("utf-8")
    except (OSError, gzip.BadGzipFile):
        text = data.decode("utf-8")
    return text.splitlines()


def extract_package_names(filenames: list[str]) -> set[str]:
    """Filter to .sty/.cls and strip extensions to get package names.

    Accepts either bare filenames (``-X bundle search`` output) or full
    bundle paths (``.ttb`` index entries): the basename is taken first,
    so ``texlive/tex/latex/base/article.cls`` and ``article.cls`` both
    yield ``article``.
    """
    names: set[str] = set()
    for line in filenames:
        base = line.strip().rsplit("/", 1)[-1]
        m = _PACKAGE_FILE_RE.match(base)
        if m:
            names.add(m.group(1))
    return names


def write_manifest(names: set[str], output: Path) -> None:
    """Write the manifest with a stable, diff-friendly format."""
    output.parent.mkdir(parents=True, exist_ok=True)
    sorted_names = sorted(names)
    with output.open("w") as fp:
        # Header line — comment so the file can be loaded with simple
        # "skip lines starting with #" parsing. Includes a hint about
        # how the file was generated.
        fp.write(
            "# Bundle-resident package manifest for the pinned tectonic\n"
            "# bundle (texlive2026 .ttb). Generated by\n"
            "# tools/extract_bundle_manifest.py from the bundle's\n"
            "# .ttb.index.gz file listing. Refresh when DEFAULT_BUNDLE in\n"
            "# latex/private/bundles.bzl changes. See DESIGN.md §4.10 for\n"
            "# the role this plays in CTAN auto-resolution.\n"
        )
        for name in sorted_names:
            fp.write(name + "\n")
    print(
        f"Wrote {len(sorted_names)} entries to {output}",
        file=sys.stderr,
    )


def main() -> int:
    args = parse_args()

    modes = [
        m for m in (args.tectonic, args.index_url, args.index_file)
        if m is not None
    ]
    if len(modes) > 1:
        print(
            "error: --tectonic, --index-url, and --index-file are "
            "mutually exclusive",
            file=sys.stderr,
        )
        return 1

    if args.tectonic is not None:
        # Legacy mode: enumerate tectonic's built-in default bundle.
        if not args.tectonic.is_file():
            print(
                f"error: tectonic binary not found at {args.tectonic}",
                file=sys.stderr,
            )
            return 1
        cache_owned: tempfile.TemporaryDirectory[str] | None = None
        cache_dir = args.cache_dir
        if cache_dir is None:
            cache_owned = tempfile.TemporaryDirectory(
                prefix="rules_latex_manifest_",
            )
            cache_dir = Path(cache_owned.name)
        try:
            filenames = enumerate_bundle(args.tectonic, cache_dir)
            names = extract_package_names(filenames)
            write_manifest(names, args.output)
        finally:
            if cache_owned is not None:
                cache_owned.cleanup()
        return 0

    # Default mode: parse the pinned bundle's .ttb.index.gz sidecar.
    index_lines = _read_index(
        args.index_url or _DEFAULT_INDEX_URL,
        args.index_file,
    )
    paths = enumerate_bundle_from_index(index_lines)
    names = extract_package_names(paths)
    write_manifest(names, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
