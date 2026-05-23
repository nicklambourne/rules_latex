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
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

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
        f"https://mirrors.ctan.org/install/macros/latex/contrib/{package}.tds.zip",
        f"https://mirrors.ctan.org/macros/latex/contrib/{package}.zip",
        f"https://mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/{package}.zip",
        f"https://mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/{package}/{package}.zip",
    ]

    archive = dest_dir / f"{package}.zip"

    last_error: BaseException | None = None
    for url in urls:
        try:
            print(f"Downloading CTAN package {package} from {url}...", file=sys.stderr)
            urllib.request.urlretrieve(url, archive)
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
            # Connection refused / timed out / DNS failure / TLS error.
            # These are usually transient — surface a one-liner instead
            # of a 40-line urllib traceback. The retry suggestion is
            # genuine: CTAN mirror availability fluctuates.
            raise SystemExit(
                f"Network error fetching CTAN package '{package}' from {url}: "
                f"{e.reason}. CTAN mirrors can be flaky; try again, or set "
                f"a specific mirror via HTTPS_PROXY / configure DNS."
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
) -> None:
    """Run tectonic with cwd set to the staged work directory.

    Setting ``cwd`` is what makes paths inside ``main.tex`` resolve
    correctly: ``\\input{sections/foo}`` becomes
    ``<work>/sections/foo`` because that's where staging put it.
    """
    env = os.environ.copy()
    env["TECTONIC_CACHE_DIR"] = str(cache_dir.resolve())
    env["LC_ALL"] = "C.UTF-8"

    if ctan_dir is not None:
        env["TEXMFHOME"] = str(ctan_dir.resolve())

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
        missing = _extract_missing_file(log_path)
        message = (
            f"tectonic exited with code {result.returncode}; see log in "
            f"{main_in_workdir.parent} for details."
        )
        if missing is not None:
            hint = _format_missing_file_hint(
                missing,
                list(ctan_packages or []),
                package_deps or {},
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
