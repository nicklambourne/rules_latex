#!/usr/bin/env python3
"""Capture a tectonic cache snapshot for a single LaTeX document.

This is the worker behind `latex_cache_snapshot`. It runs `tectonic -X
compile` once, in online mode, on the supplied source tree, and tars up
the resulting cache directory. The output tarball can then be checked
into the repository and consumed by `latex_document(cache = ...)` to
make subsequent builds fully hermetic and ~tens-of-MB instead of the
~3 GB full-bundle approach.

The intentional design constraints:

* No third-party dependencies. Uses only the Python standard library so
  it can run from any `bazel run` environment.
* Works against `tectonic` as a black box. We do not parse or rebuild
  Tectonic's bundle format; we let Tectonic populate its cache and just
  tar what comes out. If Tectonic's internal cache layout changes, this
  tool keeps working.
* Reproducible output. The generated tarball uses sorted entries and
  fixed mtimes so re-running the tool on the same inputs produces the
  same bytes.
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


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
        help="Additional source file the document needs at compile time. May "
        "be repeated. The script copies each src into a flat working "
        "directory before invoking tectonic.",
    )
    parser.add_argument(
        "--src-root",
        type=Path,
        default=None,
        help="Optional source root to preserve in the working directory layout. "
        "When set, --src and --main paths are interpreted relative to this "
        "root so any \\input{subdir/foo} references continue to resolve.",
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
    return parser.parse_args()


def stage_sources(
    main: Path,
    srcs: list[Path],
    src_root: Path | None,
    work_dir: Path,
) -> Path:
    """Copy sources into ``work_dir`` and return the staged main path.

    When ``src_root`` is supplied, the relative layout is preserved so
    LaTeX's `\\input{}` / `\\include{}` machinery still resolves
    sub-directory references. Otherwise we copy everything into the work
    dir flat.
    """
    if src_root is not None:
        for src in [main, *srcs]:
            try:
                rel = src.relative_to(src_root)
            except ValueError:
                raise SystemExit(
                    f"--src {src} is not under --src-root {src_root}"
                )
            dest = work_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        return work_dir / main.relative_to(src_root)

    for src in srcs:
        shutil.copyfile(src, work_dir / src.name)
    shutil.copyfile(main, work_dir / main.name)
    return work_dir / main.name


def run_tectonic(tectonic: Path, main_in_workdir: Path, cache_dir: Path) -> None:
    """Run tectonic with a dedicated cache directory.

    We pass --keep-logs so a compile failure leaves a .log behind in the
    working directory for debugging. The compile output (.pdf) itself
    is discarded — we only care about populating the cache.
    """
    env = os.environ.copy()
    env["TECTONIC_CACHE_DIR"] = str(cache_dir)
    env["LC_ALL"] = "C.UTF-8"
    cmd = [
        str(tectonic),
        "-X",
        "compile",
        "--keep-logs",
        "--outdir",
        str(main_in_workdir.parent),
        str(main_in_workdir),
    ]
    print("$ " + " ".join(cmd), file=sys.stderr)
    result = subprocess.run(cmd, env=env, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"tectonic exited with code {result.returncode}; see log in "
            f"{main_in_workdir.parent} for details."
        )


def pack_cache(cache_dir: Path, output: Path) -> None:
    """Tar ``cache_dir`` into ``output`` reproducibly."""
    output.parent.mkdir(parents=True, exist_ok=True)

    # Walk the cache and emit a deterministic tar: sorted entries, fixed
    # mtime, fixed owner. The cache contents themselves are content-
    # addressed by tectonic so this is sufficient for byte-identical
    # output across runs given identical inputs.
    entries: list[tuple[str, Path]] = []
    for path in sorted(cache_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(cache_dir).as_posix()
        entries.append((rel, path))

    # Open gzip with mtime=0 explicitly so the compressed header doesn't
    # leak the wall-clock time of this run. tarfile.open(..., "w:gz")
    # uses the current time otherwise.
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

    with tempfile.TemporaryDirectory(prefix="rules_latex_snapshot_") as tmp:
        tmp_path = Path(tmp)
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        main_in_workdir = stage_sources(
            args.main, args.srcs, args.src_root, work_dir
        )
        run_tectonic(args.tectonic, main_in_workdir, cache_dir)
        pack_cache(cache_dir, output)

    size_mb = output.stat().st_size / (1024 * 1024)
    print(
        f"Wrote cache snapshot to {output} ({size_mb:.1f} MiB).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
