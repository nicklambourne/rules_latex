#!/usr/bin/env python3
"""TectonicCompile action wrapper.

Stages sources under the main-rooted layout (see ``tools/staging.py``),
runs ``tectonic -X compile`` with a pre-populated cache (or a bundle),
then copies the resulting PDF (and optional .synctex.gz) to caller-
specified Bazel output paths.

Replaces the inline shell snippet that drove TectonicCompile before
v0.3. The shell version ran tectonic with execroot as cwd and a full
execroot-relative main path, which made path resolution inconsistent
with the (also-staged) PopulateCache action. Going through this
wrapper unifies both action paths.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from staging import PkgFile, stage_sources  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tectonic", required=True, type=Path)
    parser.add_argument("--main", required=True, type=Path)
    parser.add_argument(
        "--src", dest="srcs", action="append", default=[], type=Path,
    )
    parser.add_argument(
        "--pkg-file", dest="pkg_files", action="append", default=[],
        help="Format: '<src-path>=<staged-relative-path>'.",
    )
    parser.add_argument("--biber", type=Path, default=None)
    parser.add_argument(
        "--cache-tarball", type=Path, default=None,
        help="Extract this cache tarball into TECTONIC_CACHE_DIR before "
        "running tectonic. Mutually exclusive with --bundle.",
    )
    parser.add_argument(
        "--bundle", type=Path, default=None,
        help="Pass --bundle <path> to tectonic. Mutually exclusive with "
        "--cache-tarball.",
    )
    parser.add_argument(
        "--outfmt", default="pdf",
        help="Output format (pdf|xdv|html|aux). Default: pdf.",
    )
    parser.add_argument(
        "--output", required=True, type=Path,
        help="Bazel-declared output path to copy the produced file to.",
    )
    parser.add_argument(
        "--synctex-output", type=Path, default=None,
        help="When set, also pass --synctex to tectonic and copy the "
        "resulting .synctex.gz to this path.",
    )
    parser.add_argument(
        "--log-output", type=Path, default=None,
        help="When set, copy the compile log to this path. Used by "
        "latex_test to grep for required/forbidden patterns.",
    )
    parser.add_argument(
        "--reproducible", action="store_true",
        help="Pass -Z deterministic-mode and set SOURCE_DATE_EPOCH=0.",
    )
    parser.add_argument(
        "--tectonic-arg", dest="tectonic_args", action="append", default=[],
        help="Extra arguments passed through to tectonic, in order.",
    )
    return parser.parse_args()


def _parse_pkg_files(raw_entries: list[str]) -> list[PkgFile]:
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
    *,
    tectonic: Path,
    main_in_workdir: Path,
    cache_dir: Path,
    bundle: Path | None,
    outfmt: str,
    synctex: bool,
    reproducible: bool,
    extra_args: list[str],
    biber: Path | None,
) -> None:
    """Run tectonic with cwd set to the staged work directory.

    cwd is the parent of the staged main. main is passed by basename
    so tectonic's internal path resolution (\\input, \\graphicspath,
    \\addbibresource, etc.) anchors at the work directory.
    """
    env = os.environ.copy()
    env["TECTONIC_CACHE_DIR"] = str(cache_dir.resolve())
    env["LC_ALL"] = "C.UTF-8"
    if reproducible:
        env["SOURCE_DATE_EPOCH"] = "0"

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

    cmd: list[str] = [
        str(tectonic.resolve()),
        "-X",
        "compile",
        "--outfmt",
        outfmt,
        "--keep-logs",
    ]
    if bundle is not None:
        cmd += ["--bundle", str(bundle.resolve()), "--only-cached"]
    else:
        cmd += ["--only-cached"]
    if reproducible:
        cmd += ["-Z", "deterministic-mode"]
    if synctex:
        cmd += ["--synctex"]
    cmd += extra_args
    cmd += [main_in_workdir.name]

    print(
        "$ (cd " + str(main_in_workdir.parent) + " && " +
        " ".join(cmd) + ")",
        file=sys.stderr,
    )
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


def _extract_cache(tarball: Path, cache_dir: Path) -> None:
    """Extract a cache snapshot tarball into ``cache_dir`` in-place."""
    with tarfile.open(tarball, "r:gz") as tar:
        # Python 3.12+ requires an extraction filter; 3.10/3.11 accept
        # one. Defaulting to 'data' is the safe choice.
        try:
            tar.extractall(cache_dir, filter="data")
        except TypeError:
            tar.extractall(cache_dir)


def main() -> int:
    args = parse_args()

    if args.cache_tarball is None and args.bundle is None:
        raise SystemExit(
            "either --cache-tarball or --bundle must be supplied"
        )
    if args.cache_tarball is not None and args.bundle is not None:
        raise SystemExit(
            "--cache-tarball and --bundle are mutually exclusive"
        )

    pkg_files = _parse_pkg_files(args.pkg_files)

    with tempfile.TemporaryDirectory(prefix="rules_latex_compile_") as tmp:
        tmp_path = Path(tmp)
        work_dir = tmp_path / "work"
        cache_dir = tmp_path / "cache"
        work_dir.mkdir()
        cache_dir.mkdir()

        if args.cache_tarball is not None:
            _extract_cache(args.cache_tarball, cache_dir)

        main_in_workdir = stage_sources(
            args.main, args.srcs, pkg_files, work_dir,
        )

        run_tectonic(
            tectonic=args.tectonic,
            main_in_workdir=main_in_workdir,
            cache_dir=cache_dir,
            bundle=args.bundle,
            outfmt=args.outfmt,
            synctex=args.synctex_output is not None,
            reproducible=args.reproducible,
            extra_args=list(args.tectonic_args),
            biber=args.biber,
        )

        # Tectonic names outputs after the main file's stem. Copy them
        # to the Bazel-declared output paths.
        main_stem = main_in_workdir.stem
        produced = main_in_workdir.parent / f"{main_stem}.{args.outfmt}"
        if not produced.is_file():
            raise SystemExit(
                f"expected tectonic to produce {produced} but it did "
                "not; check the log above for compile errors."
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, args.output)

        if args.synctex_output is not None:
            synctex_src = main_in_workdir.parent / f"{main_stem}.synctex.gz"
            if not synctex_src.is_file():
                raise SystemExit(
                    f"expected tectonic to produce {synctex_src} when "
                    "--synctex was requested but it did not."
                )
            args.synctex_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(synctex_src, args.synctex_output)

        if args.log_output is not None:
            log_src = main_in_workdir.parent / f"{main_stem}.log"
            if log_src.is_file():
                args.log_output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(log_src, args.log_output)
            else:
                # No log produced (e.g. extremely early failure) is
                # unusual but not necessarily fatal; emit an empty file
                # so latex_test's downstream grep sees something
                # deterministic.
                args.log_output.parent.mkdir(parents=True, exist_ok=True)
                args.log_output.write_bytes(b"")

    return 0


if __name__ == "__main__":
    sys.exit(main())
