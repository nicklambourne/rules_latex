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
import shutil
import subprocess
import sys
import tarfile
import tempfile
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


def download_ctan_package(package: str, dest_dir: Path) -> None:
    """Download a single CTAN package in TDS format.

    Tries the TDS .zip first (structured tex/latex/contrib tree),
    then falls back to the raw package .zip and normalizes it into
    a compatible TDS layout under dest_dir.
    """
    urls = [
        f"https://mirrors.ctan.org/install/macros/latex/contrib/{package}.tds.zip",
        f"https://mirrors.ctan.org/macros/latex/contrib/{package}.zip",
        f"https://mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/{package}.zip",
        f"https://mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/{package}/{package}.zip",
    ]

    archive = dest_dir / f"{package}.zip"

    last_error = None
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
            raise
    else:
        raise SystemExit(
            f"Failed to download CTAN package {package} from any mirror. "
            f"Last error: {last_error}"
        )

    # Extract to a staging area first
    extract_tmp = dest_dir / f"_{package}_extract"
    extract_tmp.mkdir()
    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(extract_tmp)

    # Clean up the archive
    archive.unlink()

    # Normalize the extracted contents into a TDS tree under dest_dir
    _normalize_ctan_tree(extract_tmp, dest_dir, package)

    # Remove the temporary extraction directory
    shutil.rmtree(str(extract_tmp), ignore_errors=True)


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


def run_tectonic(
    tectonic: Path,
    main_in_workdir: Path,
    cache_dir: Path,
    biber: Path | None = None,
    ctan_dir: Path | None = None,
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
        raise SystemExit(
            f"tectonic exited with code {result.returncode}; see log in "
            f"{main_in_workdir.parent} for details."
        )


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
        if args.ctan_packages:
            ctan_dir = tmp_path / "ctan_pkgs"
            ctan_dir.mkdir()
            for pkg in args.ctan_packages:
                download_ctan_package(pkg, ctan_dir)

        main_in_workdir = stage_sources(
            args.main, args.srcs, pkg_files, work_dir,
        )
        run_tectonic(args.tectonic, main_in_workdir, cache_dir, biber=args.biber, ctan_dir=ctan_dir)
        pack_cache(cache_dir, output, ctan_dir=ctan_dir)

    size_mb = output.stat().st_size / (1024 * 1024)
    print(
        f"Wrote cache snapshot to {output} ({size_mb:.1f} MiB).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
