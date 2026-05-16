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
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
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


def run_tectonic(
    tectonic: Path,
    main_in_workdir: Path,
    cache_dir: Path,
    biber: Path | None = None,
) -> None:
    """Run tectonic with cwd set to the staged work directory.

    Setting ``cwd`` is what makes paths inside ``main.tex`` resolve
    correctly: ``\\input{sections/foo}`` becomes
    ``<work>/sections/foo`` because that's where staging put it.
    """
    env = os.environ.copy()
    env["TECTONIC_CACHE_DIR"] = str(cache_dir.resolve())
    env["LC_ALL"] = "C.UTF-8"

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


def pack_cache(cache_dir: Path, output: Path) -> None:
    """Tar ``cache_dir`` into ``output`` reproducibly.

    Walks the cache and emits a deterministic tar: sorted entries,
    fixed mtime, fixed owner. The cache contents themselves are
    content-addressed by tectonic so this is sufficient for byte-
    identical output across runs given identical inputs.
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    entries: list[tuple[str, Path]] = []
    for path in sorted(cache_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(cache_dir).as_posix()
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

        main_in_workdir = stage_sources(
            args.main, args.srcs, pkg_files, work_dir,
        )
        run_tectonic(args.tectonic, main_in_workdir, cache_dir, biber=args.biber)
        pack_cache(cache_dir, output)

    size_mb = output.stat().st_size / (1024 * 1024)
    print(
        f"Wrote cache snapshot to {output} ({size_mb:.1f} MiB).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
