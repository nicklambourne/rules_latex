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

Persistent-worker mode
----------------------

When invoked with ``--persistent_worker`` (passed by Bazel when the
``TectonicCompile`` action declares ``supports-workers = "1"``),
this script enters a long-lived loop:

* Read length-prefixed JSON ``WorkRequest`` from stdin.
* Each request carries an ``arguments`` list interpreted exactly
  like a fresh-process CLI invocation.
* Run the compile with those arguments. stderr is captured to a
  per-request buffer.
* Write a length-prefixed JSON ``WorkResponse`` to stdout with
  ``exit_code`` and ``output``.

This eliminates ~80-150 ms of CPython cold-start per action, which
on the warm-rebuild hot path is a meaningful chunk of the latency
floor after the other optimisations land (1.1 / 1.2 / 1.3 in the
DESIGN.md perf notes).

The worker protocol uses JSON (one request/response per line on
stdin/stdout) by passing ``--worker_protocol=json`` from the
Starlark side. Bazel also supports a protobuf flavour but we
deliberately avoid taking a protobuf dep here.

State-leakage is mostly avoided because:

* ``stage_sources`` uses a fresh ``tempfile.TemporaryDirectory``
  per request (line ~``with tempfile.TemporaryDirectory(...)``).
* ``run_tectonic`` constructs a local ``env`` dict per invocation
  (copying ``os.environ``, never mutating the module-level one).
* No module-level mutable caches anywhere in the script.

The one place that *could* leak between requests is if the worker
process inherits an unusual ``os.environ`` from Bazel and a later
request expects a stricter starting environment. Bazel rebuilds
``os.environ`` from the action's declared ``env`` and ``--action_env``
before invoking the worker, so this is also fine in practice. If
unsure, run with ``--no-workers`` to bypass persistence.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from staging import PkgFile, stage_sources  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
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
        "running tectonic. Mutually exclusive with --bundle and "
        "--cache-dir.",
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=None,
        help="Use this pre-extracted cache directory as "
        "TECTONIC_CACHE_DIR directly, skipping the per-action "
        "tarball decompression that --cache-tarball does. Used by "
        "latex_serve_web's persistent-cache fast-path; the "
        "directory is expected to be a complete tectonic cache "
        "tree (the same shape an extract of --cache-tarball would "
        "produce) and is consumed read-only. Mutually exclusive "
        "with --cache-tarball and --bundle.",
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
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse a single invocation's argv. Used both at top-level
    (``argv=None`` -> ``sys.argv[1:]``) and per-request in worker
    mode (``argv=<list-from-WorkRequest>``)."""
    parser = _build_parser()
    # The persistent-worker flag is consumed by ``main`` directly
    # (it must be present only on the bootstrap invocation, never
    # inside a WorkRequest). We accept it here too so argparse
    # doesn't error out on it.
    parser.add_argument(
        "--persistent_worker", action="store_true",
        help=argparse.SUPPRESS,
    )
    # Bazel passes args via a response file when supports-workers
    # is set: the worker's argv (or each WorkRequest's arguments)
    # contains a single "@<path>" entry pointing at a file whose
    # contents are the real arguments, newline-separated. We
    # expand that here so downstream parsing sees the unfolded
    # form whether or not the caller used the response-file form.
    return parser.parse_args(_expand_response_files(argv))


def _expand_response_files(
    argv: list[str] | None,
) -> list[str] | None:
    """Expand any ``@<path>`` entry in ``argv`` by inlining the file's
    newline-separated contents.

    Bazel's ``Args.use_param_file("@%s", use_always = True)`` emits
    exactly this shape: the action's argv is a single ``@<path>``
    pointing at a generated params file. The same idiom is also
    used per-request in worker mode when arguments overflow the
    command-line length limit.

    Pass-through behaviour: if ``argv`` is ``None`` we return
    ``None`` so argparse falls back to ``sys.argv[1:]``; if no
    entry starts with ``@`` we return ``argv`` unchanged.
    """
    if argv is None:
        return None
    expanded: list[str] = []
    for token in argv:
        if token.startswith("@") and len(token) > 1:
            path = token[1:]
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    for line in fp.read().splitlines():
                        if line:
                            expanded.append(line)
                continue
            except OSError:
                # Fall through to literal handling if the file
                # doesn't exist; argparse will then surface a more
                # useful error.
                pass
        expanded.append(token)
    return expanded


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
        # Capture tectonic's stdout and forward it to our stderr.
        # In persistent-worker mode our process stdout is the
        # worker protocol channel; any bytes tectonic writes
        # there would be parsed as a malformed WorkResponse and
        # crash Bazel's worker pool. Tectonic's stdout is just
        # user-facing progress notes ("note: Running TeX ..."),
        # so collapsing it into stderr is harmless. In single-
        # shot mode this is also a net win: all tectonic chatter
        # ends up on one stream.
        #
        # We can't pass ``stdout=sys.stderr`` directly because
        # in worker mode ``sys.stderr`` is a ``StringIO`` (so it
        # has no real fd). Capture into bytes via PIPE and write
        # to ``sys.stderr`` after the run.
        result = subprocess.run(
            cmd,
            env=env,
            cwd=main_in_workdir.parent,
            check=False,
            stdout=subprocess.PIPE,
        )
        if result.stdout:
            try:
                sys.stderr.write(result.stdout.decode("utf-8", errors="replace"))
            except Exception:
                pass
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


def run_one(args: argparse.Namespace) -> int:
    """Execute a single compile request. Returns 0 on success or a
    non-zero exit code on failure. ``args`` is the result of
    ``parse_args`` for the request."""
    cache_modes = [
        x for x in (args.cache_tarball, args.bundle, args.cache_dir)
        if x is not None
    ]
    if len(cache_modes) == 0:
        raise SystemExit(
            "exactly one of --cache-tarball, --cache-dir, or "
            "--bundle must be supplied"
        )
    if len(cache_modes) > 1:
        raise SystemExit(
            "--cache-tarball, --cache-dir, and --bundle are "
            "mutually exclusive"
        )

    pkg_files = _parse_pkg_files(args.pkg_files)

    with tempfile.TemporaryDirectory(prefix="rules_latex_compile_") as tmp:
        tmp_path = Path(tmp)
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        if args.cache_dir is not None:
            # Fast path: hand tectonic a pre-extracted cache
            # directly. Skips the per-action gzip decompression +
            # 300+ file writes that --cache-tarball does on every
            # warm rebuild. Tectonic doesn't modify
            # TECTONIC_CACHE_DIR under --only-cached, so this is
            # safe to share read-only across concurrent compiles.
            cache_dir = args.cache_dir
            if not cache_dir.is_dir():
                raise SystemExit(
                    f"--cache-dir {cache_dir} is not a directory"
                )
        else:
            cache_dir = tmp_path / "cache"
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


def _worker_loop() -> int:
    """Run as a Bazel persistent worker.

    Reads JSON ``WorkRequest`` messages from stdin (one per line),
    executes each as a compile request, writes JSON ``WorkResponse``
    messages to stdout. Per-request stderr is captured into the
    response's ``output`` field so Bazel can surface it cleanly.

    Bazel uses the JSON worker protocol when the action passes
    ``--persistent_worker`` and the rule's
    ``execution_requirements`` include ``"requires-worker-protocol":
    "json"``. See ``latex/private/latex_document.bzl`` for the
    Starlark-side wiring.

    Multiplex workers (``supports-multiplex-workers``) are NOT
    declared by the rule: ``run_one`` is not currently re-entrant
    on a single process because ``os.chdir`` is implicitly called
    inside ``run_tectonic`` (via ``cwd=`` on subprocess.run, which
    is safe) but the script also touches ``sys.stderr`` redirection
    in this loop. If we ever want multiplex, swap the stderr
    capture for a per-request capture context-manager and audit
    the staging tmpdir for any cwd assumptions.
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            # Malformed input; per the protocol we should still
            # respond with an exit code so Bazel knows to discard
            # us. Be loud about it.
            _write_response(
                exit_code=2,
                output=f"worker: invalid JSON on stdin: {e}\n",
                request_id=0,
            )
            continue

        request_id = int(request.get("requestId", 0))
        arguments = request.get("arguments", [])

        # Cancellation: Bazel may send a cancel signal for an
        # in-flight request via a request with ``cancel = True``.
        # Our compile is synchronous and short-lived; we honour
        # the cancel by returning an error response immediately
        # (we can't actually interrupt the in-flight compile).
        if request.get("cancel"):
            _write_response(
                exit_code=8,  # arbitrary non-zero; bazel just wants ack.
                output="worker: cancel acknowledged\n",
                request_id=request_id,
                was_cancelled=True,
            )
            continue

        stderr_buf = StringIO()
        old_stderr = sys.stderr
        sys.stderr = stderr_buf
        try:
            try:
                args = parse_args(arguments)
            except SystemExit as e:
                # argparse-driven exit; surface stderr we captured.
                exit_code = int(e.code) if isinstance(e.code, int) else 2
                _write_response(
                    exit_code=exit_code or 2,
                    output=stderr_buf.getvalue(),
                    request_id=request_id,
                )
                continue
            try:
                run_one(args)
                exit_code = 0
            except SystemExit as e:
                exit_code = int(e.code) if isinstance(e.code, int) else 1
            except Exception:
                # Unexpected exception: report exception text so
                # the user can see what blew up, but keep the
                # worker alive for the next request.
                stderr_buf.write(traceback.format_exc())
                exit_code = 1
        finally:
            sys.stderr = old_stderr

        _write_response(
            exit_code=exit_code,
            output=stderr_buf.getvalue(),
            request_id=request_id,
        )

    return 0


def _write_response(
    *,
    exit_code: int,
    output: str,
    request_id: int,
    was_cancelled: bool = False,
) -> None:
    """Emit a JSON ``WorkResponse`` followed by a newline to stdout.

    Bazel reads exactly one line per response. Flushing after every
    write is essential or Bazel will block waiting for a response
    that's stuck in our stdio buffer.
    """
    payload: dict[str, object] = {
        "exitCode": exit_code,
        "output": output,
        "requestId": request_id,
    }
    if was_cancelled:
        payload["wasCancelled"] = True
    sys.stdout.write(json.dumps(payload, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> int:
    # Pre-parse just enough to detect persistent-worker mode. We
    # can't use the full parser here because in worker mode the
    # bootstrap argv is *just* "--persistent_worker" with no other
    # flags; argparse would error on the missing required ones.
    if "--persistent_worker" in sys.argv[1:]:
        return _worker_loop()
    args = parse_args()
    return run_one(args)


if __name__ == "__main__":
    sys.exit(main())
