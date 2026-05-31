#!/usr/bin/env python3
"""TectonicPopulateCache action wrapper.

Stages sources under the main-rooted layout (see ``tools/staging.py``),
runs ``tectonic -X compile`` once to populate the resource cache, then
emits a deterministic tarball of that cache for downstream
TectonicCompile actions.

Replaces the pre-v0.3 ``tools/make_cache_snapshot.py`` which used
common-ancestor staging. The wire shape changed: ``--src-root`` is
gone (the layout is fixed) and ``--pkg-file`` is new.

Also still drives the user-facing ``latex_cache_snapshot`` rule via
``bazel run``, in which case ``--workspace`` is set to the workspace
root and ``--output`` is a workspace-relative path written back into
the source tree.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


# CTAN mirror URL prefix. Overridable via env so that:
#   * CI can point at a checked-in fixture mirror (avoids real-CTAN flake).
#   * Enterprise users can point at a private/air-gapped mirror.
# The default lands on the round-robin CTAN mirror redirect.
CTAN_MIRROR = os.environ.get(
    "RULES_LATEX_CTAN_MIRROR", "https://mirrors.ctan.org"
).rstrip("/")

# Retry policy for transient network errors during CTAN fetches.
# Three attempts with exponential backoff (1s, 2s, 4s) is the
# standard "polite-but-actually-helps" shape — covers a transient
# DNS hiccup or one bad mirror redirect without burning minutes
# during a genuine outage.
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 1.0


def _retry_urlretrieve(
    url: str,
    dest: Path,
    *,
    max_attempts: int = _RETRY_MAX_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY_S,
    sleep=time.sleep,
) -> None:
    """``urlretrieve`` with retries on transient errors.

    Retries up to ``max_attempts`` times on ``URLError`` (connection
    refused, timeout, DNS failure, TLS hiccup) and on ``HTTPError``
    with a 5xx status. 4xx codes propagate immediately — those are
    "the file isn't there", and the caller already handles 404 by
    falling through to the next URL.

    Backoff is exponential: ``base_delay * 2**(attempt-1)`` between
    attempts (1s, 2s, 4s with the defaults). ``sleep`` is a seam
    for tests.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            urllib.request.urlretrieve(url, dest)
            return
        except urllib.error.HTTPError as e:
            last_exc = e
            if 500 <= e.code < 600 and attempt < max_attempts:
                delay = base_delay * (2 ** (attempt - 1))
                print(
                    f"  HTTP {e.code} on attempt {attempt}/{max_attempts}; "
                    f"retrying in {delay:g}s...",
                    file=sys.stderr,
                )
                sleep(delay)
                continue
            raise
        except urllib.error.URLError as e:
            last_exc = e
            if attempt < max_attempts:
                delay = base_delay * (2 ** (attempt - 1))
                print(
                    f"  network error on attempt {attempt}/{max_attempts}: "
                    f"{e.reason}; retrying in {delay:g}s...",
                    file=sys.stderr,
                )
                sleep(delay)
                continue
            raise
    # Loop only exits via return or raise; this is unreachable but
    # appeases the type checker.
    if last_exc is not None:
        raise last_exc

# Allow this script to be run directly (bazel build) or from runfiles
# (bazel run), by locating staging.py next to it on disk.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from staging import PkgFile, stage_sources  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tectonic",
        required=True,
        type=Path,
        help="Path to the tectonic executable.",
    )
    parser.add_argument(
        "--main",
        required=True,
        type=Path,
        help="Path to the main .tex file to compile (used as the cache primer).",
    )
    parser.add_argument(
        "--src",
        dest="srcs",
        action="append",
        default=[],
        type=Path,
        help="Additional source file the document needs at compile time. "
        "May be repeated. Staged into the work directory using the "
        "main-rooted layout (see staging.py).",
    )
    parser.add_argument(
        "--pkg-file",
        dest="pkg_files",
        action="append",
        default=[],
        help="Override staging path for one input. Format: "
        "'<src-path>=<staged-relative-path>'. May be repeated.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to write the cache snapshot tarball to (.tar.gz).",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help=(
            "If set and --output is a relative path, write the output under "
            "this directory. Intended for `bazel run` where the script is "
            "invoked from the runfiles tree but the user wants the output in "
            "the source tree (BUILD_WORKSPACE_DIRECTORY)."
        ),
    )
    parser.add_argument(
        "--biber",
        type=Path,
        default=None,
        help=(
            "Optional path to a biber executable. When set, the script "
            "symlinks it into a per-run temporary directory and prepends "
            "that directory to PATH so the underlying tectonic invocation "
            "can resolve `biber` by basename. This is what lets "
            "`\\addbibresource` and similar biblatex directives work during "
            "cache priming."
        ),
    )
    parser.add_argument(
        "--ctan-package",
        dest="ctan_packages",
        action="append",
        default=[],
        help="CTAN package name to download and include in the cache snapshot. "
        "May be repeated. Packages are downloaded in TDS format from "
        "mirrors.ctan.org and made available to tectonic via TEXMFHOME."
    )
    parser.add_argument(
        "--bundle-manifest",
        type=Path,
        default=None,
        help=(
            "Optional path to the bundle-resident package manifest (see "
            "latex/toolchain/bundle_manifest.txt). When supplied and "
            "--ctan-package is non-empty, the populate step walks the "
            "transitive dep graph of the listed packages, HEAD-probes "
            "CTAN for references not in the manifest, and auto-fetches "
            "the closure. Without it, users must list every transitive "
            "dep manually."
        ),
    )
    parser.add_argument(
        "--bundle-url",
        default=None,
        help=(
            "URL of the package bundle (rules_latex's TL2026 .ttb on R2) to "
            "pass to tectonic as --bundle for the online prime. Tectonic "
            "range-fetches the needed files from it. When omitted, tectonic "
            "uses its built-in default (relay) bundle."
        ),
    )
    return parser.parse_args()


def _parse_pkg_files(raw_entries: list[str]) -> list[PkgFile]:
    """Parse ``--pkg-file src=rel`` entries into PkgFile objects.

    Splits on the first ``=`` so paths containing ``=`` in their
    basename are handled correctly (rare but possible).
    """
    out: list[PkgFile] = []
    for entry in raw_entries:
        if "=" not in entry:
            raise SystemExit(
                f"--pkg-file must be of the form 'src=rel'; got {entry!r}"
            )
        src_raw, rel_raw = entry.split("=", 1)
        out.append(PkgFile(src=Path(src_raw), rel=rel_raw))
    return out


def download_ctan_package(package: str, dest_dir: Path) -> set[str]:
    """Download a single CTAN package in TDS format.

    Tries the TDS .zip first (structured tex/latex/contrib tree),
    then falls back to the raw package .zip and normalizes it into
    a compatible TDS layout under dest_dir.

    Returns the set of upstream package names this package
    ``\\RequirePackage``/``\\usepackage``/``\\LoadClass``-references,
    scanned from its pre-normalisation extract tree (so we can still
    attribute deps to the originating package after files merge into
    the shared TDS overlay).
    """
    urls = [
        f"{CTAN_MIRROR}/install/macros/latex/contrib/{package}.tds.zip",
        f"{CTAN_MIRROR}/macros/latex/contrib/{package}.zip",
        f"{CTAN_MIRROR}/macros/latex/contrib/biblatex-contrib/{package}.zip",
        f"{CTAN_MIRROR}/macros/latex/contrib/biblatex-contrib/{package}/{package}.zip",
    ]

    archive = dest_dir / f"{package}.zip"

    last_error: BaseException | None = None
    for url in urls:
        try:
            print(f"Downloading CTAN package {package} from {url}...", file=sys.stderr)
            _retry_urlretrieve(url, archive)
            print(f"Downloaded {archive.stat().st_size} bytes for {package}", file=sys.stderr)
            break
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 404:
                print(f"  not found at {url}, trying next...", file=sys.stderr)
                continue
            raise SystemExit(
                f"HTTP {e.code} fetching CTAN package '{package}' from {url}: {e.reason}"
            )
        except urllib.error.URLError as e:
            # We retried inside _retry_urlretrieve and still failed.
            # Surface a one-liner instead of a 40-line urllib traceback.
            raise SystemExit(
                f"Network error fetching CTAN package '{package}' from {url} "
                f"(after {_RETRY_MAX_ATTEMPTS} attempts): {e.reason}. CTAN "
                f"mirrors can be flaky; try again later, point "
                f"RULES_LATEX_CTAN_MIRROR at a specific mirror, or check "
                f"network/DNS."
            )
    else:
        raise SystemExit(
            f"CTAN package '{package}' not found at any known URL. Tried:\n"
            + "\n".join(f"  - {u}" for u in urls)
            + f"\nLast error: {last_error}"
        )

    # Extract to a staging area first
    extract_tmp = dest_dir / f"_{package}_extract"
    extract_tmp.mkdir()
    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(extract_tmp)

    # Clean up the archive
    archive.unlink()

    # Scan the extract tree *before* normalising — once files merge
    # into the shared TDS overlay, we lose the per-package attribution
    # that makes the dep map and the failure-path hint informative.
    deps = _scan_package_dependencies(extract_tmp)

    # Normalize the extracted contents into a TDS tree under dest_dir
    _normalize_ctan_tree(extract_tmp, dest_dir, package)

    # Remove the temporary extraction directory
    shutil.rmtree(str(extract_tmp), ignore_errors=True)

    return deps


def _load_bundle_manifest(path: Path) -> set[str]:
    """Read the bundle-resident package manifest into a set.

    Lines starting with ``#`` are comments; blank lines are ignored.
    See ``tools/extract_bundle_manifest.py`` for the producer.
    """
    if not path.is_file():
        raise SystemExit(
            f"--bundle-manifest path does not exist: {path}. "
            f"Re-run `bazel run //tools:extract_bundle_manifest` to "
            f"regenerate, or pass a different path."
        )
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        names.add(line)
    return names


def _head_probe_ctan(
    package: str,
    *,
    timeout: float = 10.0,
) -> bool:
    """Return True iff one of the canonical CTAN URLs for ``package`` exists.

    Used by the auto-resolver to decide whether a scanner-discovered
    name (which might be a CTAN package, a TeX-internal component, or
    a typo) is worth fetching. We HEAD-probe the same fallback chain
    that ``download_ctan_package`` would actually try; if *any* of
    them returns 2xx, the package is reachable.

    HEAD requests are cheap (no body transfer) and 4xx returns
    immediately, so the upper bound is ~4 HEAD round-trips per
    rejected name. Failure modes (URLError, 5xx) are treated as
    "unknown" — we conservatively *don't* fetch on transient
    errors, so the worst case for a flaky mirror is "we miss a
    transitive dep this run, the user sees a hint on failure".
    """
    urls = [
        f"{CTAN_MIRROR}/install/macros/latex/contrib/{package}.tds.zip",
        f"{CTAN_MIRROR}/macros/latex/contrib/{package}.zip",
        f"{CTAN_MIRROR}/macros/latex/contrib/biblatex-contrib/{package}.zip",
        f"{CTAN_MIRROR}/macros/latex/contrib/biblatex-contrib/{package}/{package}.zip",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            # Other 4xx/5xx: treat as "not found on this URL" rather
            # than fatal, since the caller is just trying to make a
            # yes/no decision and we have more URLs to try.
            continue
        except urllib.error.URLError:
            # Transient network error. Don't pretend the package
            # exists when we don't know; the user will see the
            # missing-file hint if it really was needed.
            continue
    return False


def resolve_transitive_closure(
    seed_packages: list[str],
    dest_dir: Path,
    bundle_manifest: set[str],
    *,
    max_iterations: int = 64,
) -> dict[str, set[str]]:
    """Auto-fetch the transitive closure of CTAN packages.

    Walks the dep graph rooted at ``seed_packages``:
      1. Download each seed; scan it for upstream references
         (existing ``_scan_package_dependencies``).
      2. For each reference not in ``bundle_manifest`` and not
         already fetched: HEAD-probe CTAN to confirm the name is a
         real package. If so, fetch it and recurse. If the
         HEAD-probe says "no" (404 on every URL), silently skip —
         this filters out TeX-internal names, typos, and any other
         false-positive scanner output.
      3. Cap at ``max_iterations`` defensive iterations; we never
         actually expect to hit this in practice (the dep graph is
         a DAG and the bundle catches most leaves) but it bounds
         the worst case for circular or pathological inputs.

    Returns the same per-package dep map ``download_ctan_package``
    contributes to: ``{pkg: {referenced_name, ...}}``. Used by the
    proactive dep summary and by the failure-path hint.
    """
    fetched: dict[str, set[str]] = {}
    queue: list[str] = list(seed_packages)

    # `attempted` includes both fetched packages and those we
    # HEAD-probed and skipped, so we never re-probe the same name.
    attempted: set[str] = set()

    iterations = 0
    while queue and iterations < max_iterations:
        iterations += 1
        pkg = queue.pop(0)
        if pkg in fetched:
            continue
        attempted.add(pkg)

        # Seed packages always fetch — the user explicitly asked
        # for them, even if they happen to be in the bundle.
        is_seed = pkg in seed_packages

        if not is_seed:
            # Transitive: skip if bundle-resident, else HEAD-probe.
            if pkg in bundle_manifest:
                continue
            if not _head_probe_ctan(pkg):
                print(
                    f"  '{pkg}' referenced by a fetched package but not "
                    f"in the bundle and not found on CTAN — skipping "
                    f"(may be a TeX-internal name or a typo).",
                    file=sys.stderr,
                )
                continue

        deps = download_ctan_package(pkg, dest_dir)
        fetched[pkg] = deps

        for ref in deps:
            if ref in attempted or ref in fetched:
                continue
            queue.append(ref)
            attempted.add(ref)

    if iterations >= max_iterations:
        print(
            f"warning: transitive resolution stopped after "
            f"{max_iterations} iterations; the dep graph may be "
            f"deeper than expected. The compile may still succeed; "
            f"if not, file an issue.",
            file=sys.stderr,
        )

    return fetched


def _normalize_ctan_tree(src: Path, dest: Path, package: str) -> None:
    """Move extracted CTAN package contents into a proper TDS tree.

    If the extracted contents already contain tex/, doc/, source/
    directories, merge them directly into dest (which is a TDS root).
    Otherwise, inspect the file types and place them appropriately:
      - .sty/.cls -> tex/latex/contrib/<package>/
      - .bbx/.cbx/.lbx/.dbx -> tex/latex/biblatex/{bbx,cbx,lbx,dbx}/
      - other files -> tex/latex/contrib/<package>/
    """
    # If there's a single top-level directory, use its contents
    entries = [e for e in src.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        src = entries[0]

    # Check if this already has a TDS-like structure
    tds_dirs = ["tex", "doc", "source", "fonts", "bibtex"]
    has_tds = any((src / d).is_dir() for d in tds_dirs)

    if has_tds:
        # Already TDS-structured: merge each top-level dir into dest
        for item in src.iterdir():
            if item.is_dir():
                target = dest / item.name
                if target.exists():
                    _merge_dirs(item, target)
                else:
                    shutil.move(str(item), str(target))
            elif item.is_file():
                shutil.move(str(item), str(dest / item.name))
        return

    # Flat / unknown layout: categorize files
    biblatex_dirs = {
        ".bbx": "tex/latex/biblatex/bbx",
        ".cbx": "tex/latex/biblatex/cbx",
        ".lbx": "tex/latex/biblatex/lbx",
        ".dbx": "tex/latex/biblatex/dbx",
    }

    has_biblatex = any(
        f.suffix in biblatex_dirs for f in src.iterdir() if f.is_file()
    )

    for item in src.iterdir():
        if item.is_file():
            if has_biblatex and item.suffix in biblatex_dirs:
                target_dir = dest / biblatex_dirs[item.suffix]
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(target_dir / item.name))
            else:
                # Generic contrib placement
                contrib_dir = dest / "tex" / "latex" / "contrib" / package
                contrib_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(contrib_dir / item.name))
        elif item.is_dir():
            # For directories in flat packages, just copy them into contrib
            contrib_dir = dest / "tex" / "latex" / "contrib" / package
            contrib_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item), str(contrib_dir / item.name))


def _merge_dirs(src: Path, dst: Path) -> None:
    """Recursively merge src into dst, overwriting existing files."""
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
            _merge_dirs(item, target)
        else:
            shutil.move(str(item), str(target))


def _ctan_search_paths(ctan_dir: Path) -> list[str]:
    """Enumerate directories under ``ctan_dir`` that contain package files.

    Tectonic's ``-Z search-path=<dir>`` is the actual mechanism for
    "look for files here in addition to the bundle". It's *flat* — no
    recursive descent into subdirectories — so we hand tectonic one
    flag per directory that actually contains a
    ``\\RequirePackage``-targetable file. The normalised CTAN overlay
    puts files at various TDS depths (``tex/latex/contrib/<pkg>/``,
    ``tex/latex/biblatex/bbx/``, …); each such directory becomes one
    search-path entry.

    Note: this replaces an earlier TEXMFHOME-based approach that
    appeared to work but didn't — tectonic doesn't honour TEXMFHOME at
    all (that's a kpathsea concept). The bundle was the only source
    of files for those compiles; fetched packages were ignored.

    Returns absolute paths as strings, sorted for determinism.
    """
    if not ctan_dir.exists():
        return []
    dirs: set[Path] = set()
    for path in ctan_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in _PACKAGE_FILE_EXTS:
            dirs.add(path.parent.resolve())
    return [str(d) for d in sorted(dirs)]


# File extensions we scan for `\RequirePackage` / `\usepackage` etc.
# Other CTAN file types (.tex documentation, .pdf, .map) don't pull
# in packages at compile time.
_PACKAGE_FILE_EXTS = frozenset({".sty", ".cls", ".bbx", ".cbx", ".lbx", ".dbx"})

# Matches `\RequirePackage[opts]{name}`, `\usepackage[opts]{name}`,
# `\LoadClass[opts]{name}`, and `\RequirePackageWithOptions{name}`.
# Whitespace inside the optional `[...]` arg is tolerated; package
# names are restricted to the [a-zA-Z0-9_-] charset that CTAN uses.
_REQUIRE_RE = re.compile(
    r"\\(?:RequirePackage(?:WithOptions)?|usepackage|LoadClass(?:WithOptions)?)"
    r"\s*(?:\[[^\]]*\])?\s*\{([a-zA-Z0-9_\-]+(?:\s*,\s*[a-zA-Z0-9_\-]+)*)\}"
)


def _scan_package_dependencies(package_root: Path) -> set[str]:
    """Return the set of package names a single CTAN package references.

    Greps the package's own extracted tree (pre-normalisation, while
    we still know which files belong to which package) for
    ``\\RequirePackage``, ``\\usepackage``, and ``\\LoadClass``.

    Over-reports — packages bundled with tectonic (``etoolbox``,
    ``biblatex``, …) still show up — but we only consult this on
    the failure path, where the missing name is already known, so
    over-reporting is harmless.
    """
    deps: set[str] = set()
    if not package_root.exists():
        return deps
    for path in package_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _PACKAGE_FILE_EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _REQUIRE_RE.finditer(text):
            for name in match.group(1).split(","):
                name = name.strip()
                if name:
                    deps.add(name)
    return deps


def _print_dep_summary(
    package_deps: dict[str, set[str]],
    out=sys.stderr,
) -> None:
    """Emit a short "what your ctan_packages pulled in" report.

    Printed after all downloads, before the tectonic populate step,
    so users see the dep graph even on a successful build. The list
    will include packages already in the bundle (etoolbox, biblatex,
    …) — over-reporting is fine here; the goal is transparency, not
    a precise dependency manifest.
    """
    if not package_deps:
        return
    print("ctan_packages dep map:", file=out)
    for pkg in sorted(package_deps):
        deps = sorted(package_deps[pkg])
        if not deps:
            print(f"  {pkg}: (no upstream package references found)", file=out)
        else:
            print(f"  {pkg} -> {', '.join(deps)}", file=out)
    print(file=out)


# Matches the LaTeX error tectonic prints when a `\usepackage`,
# `\RequirePackage`, `\input`, etc. names a file that resolves
# nowhere. Both styles of quote (' and `) appear in the wild.
_MISSING_FILE_RE = re.compile(
    r"!\s*LaTeX Error:\s*File [`']([^'`]+)' not found\."
)


def _extract_missing_file(log_path: Path) -> str | None:
    """Return the first missing-file name from a tectonic log, or None.

    Tectonic surfaces missing `\\usepackage`/`\\input` files as a
    standard LaTeX error inside the `.log` it writes when run with
    `--keep-logs`. We grep the log on the failure path so the hint
    can name the specific file.
    """
    if not log_path.is_file():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _MISSING_FILE_RE.search(text)
    return match.group(1) if match else None


# Matches the biblatex/biber version-coupling failure signature: a
# fetched extension style (`.bbx`/`.cbx`/`.lbx`/`.dbx`) compiled
# against the bundle's older biblatex fails on macros the older
# biblatex doesn't define. The error format tectonic prints is
# `error: <file>.bbx:<line>: Undefined control sequence`.
#
# We're deliberately strict about the extension whitelist so we don't
# false-positive on generic "Undefined control sequence" errors from
# user code.
_BIBLATEX_VERSION_ERROR_RE = re.compile(
    r"error:\s+(?P<file>\S+\.(?:bbx|cbx|lbx|dbx)):\d+:\s+Undefined control sequence",
)


def _extract_biblatex_version_mismatch(log_path: Path) -> str | None:
    """Return the offending .bbx/.cbx/.lbx/.dbx filename, or None.

    Tectonic's diagnostic for the biblatex version-coupling trap
    (bundle's biblatex 3.17 trying to read a fetched biblatex 3.18+
    style file) is `Undefined control sequence` originating in the
    extension style file. We detect that specific shape — not the
    generic LaTeX 'Undefined control sequence' — to avoid false
    positives on errors in the user's `.tex`.
    """
    if not log_path.is_file():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _BIBLATEX_VERSION_ERROR_RE.search(text)
    return match.group("file") if match else None


def _format_biblatex_version_hint(
    offending_file: str,
    ctan_packages: list[str],
) -> str:
    """Compose the modern-biblatex opt-in hint."""
    biblatex_seeds = sorted(
        p for p in ctan_packages if p.startswith("biblatex-")
    )
    seed_clause = ""
    if biblatex_seeds:
        seed_clause = (
            f" The likely culprit is {', '.join(biblatex_seeds)} in your "
            f"ctan_packages — a release of one of these styles that needs a "
            f"biblatex newer than the bundle's 3.21."
        )
    return (
        f"hint: '{offending_file}' failed with 'Undefined control sequence', "
        f"which is the signature of a biblatex extension style fetched from "
        f"CTAN being too new for the bundle's biblatex 3.21 / biber 2.21."
        f"{seed_clause}\n"
        f"\n"
        f"The pinned TL2026 bundle already ships modern biblatex (3.21); if a "
        f"style still fails this way it needs a biblatex newer than the "
        f"bundle provides. Options: pin an older release of the style, or "
        f"refresh the bundle to a newer TeX Live (see DESIGN.md §4.10).\n"
        f"\n"
        f"Background and gotchas: "
        f"https://nicklambourne.github.io/rules_latex/getting-started/"
        f"bibliography/#modern-citation-styles"
    )


def _format_missing_file_hint(
    missing: str,
    ctan_packages: list[str],
    package_deps: dict[str, set[str]],
) -> str:
    """Compose a one-paragraph hint for a missing-file failure.

    Three cases, in increasing order of how much we can say:
      1. ``missing`` is already listed in ``ctan_packages``. The
         download succeeded (otherwise we'd have died earlier) but
         tectonic still didn't find the file — most likely a
         TDS-layout mismatch in the fetched archive. Steer the user
         toward an issue report.
      2. ``missing`` is referenced by one of the fetched packages.
         We can name the requiring package: "X is required by
         <pkg> (from your ctan_packages); add 'X' to ctan_packages
         too."
      3. ``missing`` is unreferenced and unlisted. Mention it might
         be a CTAN package or a typo in ``.tex``.
    """
    base = missing.rsplit(".", 1)[0] if "." in missing else missing
    listed = base in ctan_packages

    if listed:
        return (
            f"hint: '{missing}' resolves to a package you already listed "
            f"in ctan_packages ('{base}'). The download succeeded but "
            f"tectonic couldn't find the file — likely a TDS-layout "
            f"mismatch in the fetched archive. Consider opening an "
            f"issue with the package name."
        )

    referencing = sorted(
        pkg for pkg, deps in package_deps.items() if base in deps
    )
    if referencing:
        sample = ", ".join(referencing[:3])
        more = f" (+{len(referencing) - 3} more)" if len(referencing) > 3 else ""
        return (
            f"hint: '{missing}' is required by {sample}{more} — one "
            f"of your ctan_packages. It isn't in Tectonic's 2022 "
            f"bundle. Add '{base}' to ctan_packages on this target "
            f"and rebuild."
        )

    return (
        f"hint: '{missing}' isn't in Tectonic's 2022 bundle and isn't "
        f"referenced by any of your ctan_packages. If '{base}' is a "
        f"CTAN package, add it to ctan_packages on this target. "
        f"Otherwise check for a typo in your .tex sources."
    )


def run_tectonic(
    tectonic: Path,
    main_in_workdir: Path,
    cache_dir: Path,
    biber: Path | None = None,
    ctan_dir: Path | None = None,
    ctan_packages: list[str] | None = None,
    package_deps: dict[str, set[str]] | None = None,
    bundle_url: str | None = None,
) -> None:
    """Run tectonic with cwd set to the staged work directory.

    Setting ``cwd`` is what makes paths inside ``main.tex`` resolve
    correctly: ``\\input{sections/foo}`` becomes
    ``<work>/sections/foo`` because that's where staging put it.
    """
    env = os.environ.copy()
    env["TECTONIC_CACHE_DIR"] = str(cache_dir.resolve())
    env["LC_ALL"] = "C.UTF-8"
    # NB: TEXMFHOME used to be set to ctan_dir here. It was a no-op
    # — tectonic doesn't honour TEXMFHOME at all, that's a kpathsea
    # concept. Fetched packages reach tectonic via `-Z search-path`
    # flags constructed below from `_ctan_search_paths`.

    biber_dir_owned: tempfile.TemporaryDirectory[str] | None = None
    if biber is not None:
        biber_dir_owned = tempfile.TemporaryDirectory(prefix="rules_latex_biber_")
        biber_link = Path(biber_dir_owned.name) / "biber"
        try:
            biber_link.symlink_to(biber.resolve())
        except OSError:
            shutil.copy2(biber, biber_link)
            biber_link.chmod(0o755)
        env["PATH"] = "{}:{}".format(
            biber_dir_owned.name,
            env.get("PATH", "/usr/bin:/bin"),
        )

    cmd = [
        str(tectonic.resolve()),
        "-X",
        "compile",
        "--keep-logs",
    ]
    # Point the online prime at rules_latex's pinned package bundle (the
    # TL2026 .ttb on R2) rather than tectonic's built-in relay (the frozen
    # 2022 bundle). Tectonic range-fetches the files it needs from the .ttb
    # URL; the resulting cache is what the offline TectonicCompile reuses.
    if bundle_url:
        cmd += ["--bundle", bundle_url]
    # One `-Z search-path` per directory under ctan_dir that holds
    # package files. Tectonic prefers cwd > search-path entries >
    # bundle, so this overlays fetched packages without disturbing
    # documents that resolve everything from the bundle.
    if ctan_dir is not None:
        for search_dir in _ctan_search_paths(ctan_dir):
            cmd.extend(["-Z", "search-path={}".format(search_dir)])
    cmd += [
        "--outdir",
        str(main_in_workdir.parent),
        # Pass main by basename now that cwd is its parent: this is
        # what makes \input{sections/foo} resolve against work_dir.
        main_in_workdir.name,
    ]
    print("$ (cd " + str(main_in_workdir.parent) + " && " +
          " ".join(cmd) + ")", file=sys.stderr)
    try:
        result = subprocess.run(
            cmd, env=env, cwd=main_in_workdir.parent, check=False,
        )
    finally:
        if biber_dir_owned is not None:
            biber_dir_owned.cleanup()
    if result.returncode != 0:
        log_path = main_in_workdir.parent / (main_in_workdir.stem + ".log")
        message = (
            f"tectonic exited with code {result.returncode}; see log in "
            f"{main_in_workdir.parent} for details."
        )
        missing = _extract_missing_file(log_path)
        if missing is not None:
            hint = _format_missing_file_hint(
                missing,
                list(ctan_packages or []),
                package_deps or {},
            )
            message = f"{message}\n\n{hint}"
        else:
            # If we didn't hit a missing-file error, check for the
            # biblatex version-coupling signature. The two are
            # mutually exclusive (one is "I couldn't find the file",
            # the other is "I found it but couldn't parse it"), so
            # there's never ambiguity about which hint to show.
            biblatex_file = _extract_biblatex_version_mismatch(log_path)
            if biblatex_file:
                hint = _format_biblatex_version_hint(
                    biblatex_file, list(ctan_packages or []),
                )
                message = f"{message}\n\n{hint}"
        raise SystemExit(message)


def pack_cache(cache_dir: Path, output: Path, ctan_dir: Path | None = None) -> None:
    """Tar ``cache_dir`` into ``output`` reproducibly.

    When ctan_dir is provided, creates a structured tarball with both
    the tectonic cache and CTAN packages, using cache/ and ctan_pkgs/
    prefixes respectively.

    Walks the cache and emits a deterministic tar: sorted entries,
    fixed mtime, fixed owner. The cache contents themselves are
    content-addressed by tectonic so this is sufficient for byte-
    identical output across runs given identical inputs.
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    entries: list[tuple[str, Path]] = []

    # Tectonic cache entries
    for path in sorted(cache_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(cache_dir).as_posix()
        if ctan_dir:
            rel = "cache/" + rel
        entries.append((rel, path))

    # CTAN package entries
    if ctan_dir:
        for path in sorted(ctan_dir.rglob("*")):
            if path.is_dir():
                continue
            rel = "ctan_pkgs/" + path.relative_to(ctan_dir).as_posix()
            entries.append((rel, path))

    # Open gzip with mtime=0 explicitly so the compressed header doesn't
    # leak the wall-clock time of this run.
    with open(output, "wb") as raw, gzip.GzipFile(
        filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6
    ) as gz, tarfile.open(fileobj=gz, mode="w|") as tar:
        for arcname, source in entries:
            info = tar.gettarinfo(str(source), arcname=arcname)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            with open(source, "rb") as fp:
                tar.addfile(info, fp)


def main() -> int:
    args = parse_args()

    output = args.output
    if not output.is_absolute() and args.workspace is not None:
        output = args.workspace / output

    pkg_files = _parse_pkg_files(args.pkg_files)

    with tempfile.TemporaryDirectory(prefix="rules_latex_snapshot_") as tmp:
        tmp_path = Path(tmp)
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Download CTAN packages if requested
        ctan_dir = None
        package_deps: dict[str, set[str]] = {}
        if args.ctan_packages:
            ctan_dir = tmp_path / "ctan_pkgs"
            ctan_dir.mkdir()
            if args.bundle_manifest is not None:
                # Auto-resolve: fetch the listed seed packages and
                # walk their dep graph, fetching transitive CTAN deps
                # that aren't in the bundle.
                manifest = _load_bundle_manifest(args.bundle_manifest)
                package_deps = resolve_transitive_closure(
                    args.ctan_packages, ctan_dir, manifest,
                )
            else:
                # Legacy path (no manifest plumbed through): fetch
                # only what the user listed. The failure-path hint
                # still triggers if a transitive dep is missing.
                for pkg in args.ctan_packages:
                    package_deps[pkg] = download_ctan_package(pkg, ctan_dir)
            _print_dep_summary(package_deps)

        main_in_workdir = stage_sources(
            args.main, args.srcs, pkg_files, work_dir,
        )
        run_tectonic(
            args.tectonic,
            main_in_workdir,
            cache_dir,
            biber=args.biber,
            ctan_dir=ctan_dir,
            ctan_packages=args.ctan_packages,
            package_deps=package_deps,
            bundle_url=args.bundle_url,
        )
        pack_cache(cache_dir, output, ctan_dir=ctan_dir)

    size_mb = output.stat().st_size / (1024 * 1024)
    print(
        f"Wrote cache snapshot to {output} ({size_mb:.1f} MiB).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
