#!/usr/bin/env python3
"""Serve-time cache management for ``latex_live``.

This module is consumed by the generated ``serve_web.py`` (see
``latex/private/serve_web.py.tpl``) when the document being served
takes the implicit-pipeline path -- i.e. has neither a user-supplied
``cache = "..."`` snapshot nor a toolchain-level
``tectonic.bundle()``. In that case the live-preview loop would
otherwise re-trigger the online ``TectonicPopulateCache`` action on
every keystroke save (Bazel's action cache keys it on the full
source set), turning a 2-3 s compile into a 30-90 s online prime.

The fix is to side-step Bazel's action cache for the prime step.
``latex_live``:

1. On startup, derives a stable per-document persistent cache path
   under ``$BUILD_WORKSPACE_DIRECTORY/.cache/rules_latex/`` and
   tries to prime it (lock-protected, no-op if already present).
2. Passes the path via the private build setting
   ``//latex:_serve_cache_override`` on every ``bazel build``
   invocation. ``latex_document`` then uses that snapshot in place
   of the implicit pipeline.
3. If a build fails because tectonic couldn't find a resource in
   the cached set (the user just added a new ``\\usepackage``),
   the serve loop catches it, re-primes the cache, and retries
   the build once.

The cache file lives outside Bazel's input graph by design. The
serve loop passes the resolved immutable extraction path and its
content digest via private build settings, so re-priming changes the
compile action key without invalidating files in use by older builds.

Everything in here is stdlib-only -- consistent with the rest of
the rules_latex tooling.
"""

from __future__ import annotations

import dataclasses
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


# Sentinel placed under the cache directory to detect a stale or
# partially-written prime. Touched after a successful prime; checked
# on startup. If we see a cache.tar.gz without a sentinel, we treat
# the cache as missing and re-prime.
_SENTINEL_NAME = ".primed"

# Sentinel placed under the *extracted* cache directory. Distinct
# from _SENTINEL_NAME so we can re-extract without re-priming if
# the tarball got out of sync (e.g. a developer manually edited
# the extracted dir, or a partial extract was interrupted).
_EXTRACTED_SENTINEL_NAME = ".extracted"

# Marker we write into .gitignore on first prime to keep the cache
# out of users' source trees.
_GITIGNORE_MARKER = "# rules_latex serve-time cache (auto-managed)\n.cache/rules_latex/\n"


@dataclasses.dataclass(frozen=True)
class CacheLayout:
    """File-system locations for a single document's serve cache.

    All paths are absolute. ``base`` is the per-document directory;
    ``snapshot`` is the cache tarball produced by the populate tool;
    ``extracted`` is an atomically updated symlink to an immutable
    unpacked snapshot. Compiles receive its resolved target path,
    avoiding re-extraction on every warm build;
    ``sentinel`` and ``extracted_sentinel`` are completion markers
    written after a successful prime / extract respectively;
    ``lock`` is a sidecar file used with ``flock(2)`` to serialise
    prime attempts across processes.

    Both the snapshot tarball and extracted generations live
    side-by-side because:

    * The tarball is what the populate tool emits (and what
      latex_cache_snapshot rules produce).
    * An extracted generation is what tectonic actually wants as
      its ``TECTONIC_CACHE_DIR``. Keeping both means the serve
      loop can hand tectonic a ready-to-use cache directory
      without re-extracting on every compile.

    Empirically tectonic does NOT write to its cache directory
    when invoked with ``--only-cached`` (verified against
    rules_latex's pinned tectonic version), so each immutable
    generation can be shared across concurrent compiles.
    """

    base: Path
    snapshot: Path
    extracted: Path
    sentinel: Path
    extracted_sentinel: Path
    lock: Path


def derive_cache_layout(
    workspace: Path, document_label: str, config_key: str = "",
) -> CacheLayout:
    """Compute where this document's serve cache lives.

    ``document_label`` is the target label in canonical form, e.g.
    ``//cv:cv`` or ``@@workspace//cv:cv``. We slugify it into a
    stable directory name so two documents in the same workspace
    can coexist.

    >>> layout = derive_cache_layout(Path("/ws"), "//cv:cv")
    >>> layout.base.as_posix().endswith(".cache/rules_latex/cv_cv")
    True
    >>> layout.snapshot.name
    'cache.tar.gz'
    >>> layout.extracted.name
    'cache'
    """
    slug = _slugify_label(document_label)
    base = workspace / ".cache" / "rules_latex" / slug
    if config_key:
        base = base / config_key
    return CacheLayout(
        base=base,
        snapshot=base / "cache.tar.gz",
        extracted=base / "cache",
        sentinel=base / _SENTINEL_NAME,
        extracted_sentinel=base / "cache" / _EXTRACTED_SENTINEL_NAME,
        lock=base / ".lock",
    )


def _slugify_label(label: str) -> str:
    """Turn a label like ``//cv:cv`` into a filesystem-safe slug.

    The slug must be stable across runs (the cache directory is
    keyed on it) and distinct enough that different documents in
    the same workspace don't collide. We strip the leading ``@``
    repo marker, replace ``//`` and ``:`` with ``_``, and replace
    any other non-safe character with ``_``. Two trailing chars
    of a short hash defend against unlikely collisions after
    sanitisation (e.g. labels that differ only by characters that
    sanitise to ``_``).

    >>> _slugify_label("//cv:cv")[:5]
    'cv_cv'
    >>> _slugify_label("//path/to:doc")[:11]
    'path_to_doc'
    """
    stripped = label.lstrip("@/")
    # Bzlmod canonical labels start with @@workspace; strip both.
    if stripped.startswith("@"):
        stripped = stripped.lstrip("@")
    if "//" in stripped:
        _, _, stripped = stripped.partition("//")
    sanitised = re.sub(r"[^A-Za-z0-9_.-]", "_", stripped.replace(":", "_"))
    digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:6]
    return f"{sanitised}_{digest}"


def is_primed(layout: CacheLayout) -> bool:
    """Return True iff the cache is present and the sentinel exists."""
    return layout.snapshot.is_file() and layout.sentinel.is_file()


def is_extracted(layout: CacheLayout) -> bool:
    """Return True iff a complete extracted cache directory exists.

    Distinct from ``is_primed`` because the published generation has
    its own completion sentinel. An interrupted extraction must not
    be treated as ready-to-use.
    """
    return layout.extracted.is_symlink() and layout.extracted_sentinel.is_file()


def cache_nonce(layout: CacheLayout, generation: Path | None = None) -> str:
    """Return the immutable extracted generation's content digest."""
    try:
        generation = generation or layout.extracted.resolve(strict=True)
    except FileNotFoundError:
        return "0"
    return generation.name.removeprefix("cache-")


def ensure_gitignore_excludes_cache(workspace: Path) -> None:
    """Append a ``.cache/rules_latex/`` entry to .gitignore if missing.

    First-prime quality-of-life: users who don't know about the
    auto-managed cache shouldn't have to debug why their working
    tree shows a few hundred MB of new untracked files. Idempotent
    and only writes when the entry isn't already there. Silent
    on permission errors (we're not the owner of the user's
    .gitignore policy).
    """
    gitignore = workspace / ".gitignore"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    except OSError:
        return
    if ".cache/rules_latex" in existing:
        return
    try:
        with open(gitignore, "a", encoding="utf-8") as fp:
            if existing and not existing.endswith("\n"):
                fp.write("\n")
            fp.write(_GITIGNORE_MARKER)
    except OSError:
        # Read-only workspace, etc. The user will live with the
        # untracked entry; we don't want to fail serve startup for
        # this.
        pass


@dataclasses.dataclass(frozen=True)
class PrimeSpec:
    """Everything ``run_prime`` needs to invoke the populate-cache tool.

    Mirrors the arguments ``latex_document.bzl`` passes to
    ``tools/tectonic_populate_cache.py`` for the implicit
    populate-cache action, with one important difference: paths to
    document sources are kept as **workspace-relative** strings,
    not absolute paths. This matches ``staging.stage_sources``'s
    contract (it rejects absolute paths to keep the staging layout
    deterministic). Paths to tools (tectonic, the populate script
    itself, biber) remain absolute because they live in the
    runfiles tree, not the workspace.

    ``latex_live`` snapshots these at rule analysis time and
    feeds them into this script via the serve_web.py template
    substitutions; ``run_prime`` invokes the populate tool with
    cwd set to the workspace root so the relative paths resolve.
    """

    tectonic: Path
    populate_tool: Path
    main: str                      # workspace-relative
    srcs: tuple[str, ...]          # workspace-relative
    pkg_files: tuple[tuple[str, str], ...]  # (workspace-rel src, staged rel)
    biber: Path | None
    use_system_biber: bool
    bundle_url: str
    ctan_packages: tuple[str, ...]
    bundle_manifest: Path | None
    ctan_lock: Path | None = None


def prime_config_key(spec: PrimeSpec) -> str:
    """Fingerprint the inputs that determine a serve-time cache prime.

    Source *paths* participate, but source contents do not: an ordinary
    edit should keep using the warm cache until a missing resource asks
    for re-priming. Tool identities are based on path, size and mtime so
    startup does not rehash large Tectonic or biber binaries.
    """
    def identity(path: Path | None) -> object:
        if path is None:
            return None
        try:
            stat = path.stat()
            return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        except OSError:
            return (str(path), "missing")

    system_biber_path = shutil.which("biber") if spec.use_system_biber else None
    system_biber = Path(system_biber_path) if system_biber_path else None
    manifest_hash = None
    if spec.bundle_manifest is not None:
        manifest_hash = hashlib.sha256(spec.bundle_manifest.read_bytes()).hexdigest()
    payload = {
        "tectonic": identity(spec.tectonic),
        "populate_tool": identity(spec.populate_tool),
        "staging_lib": identity(spec.populate_tool.parent / "staging.py"),
        "biber": identity(spec.biber or system_biber),
        "use_system_biber": spec.use_system_biber,
        "bundle_url": spec.bundle_url,
        "bundle_manifest": manifest_hash,
        "ctan_packages": spec.ctan_packages,
        "main": spec.main,
        "srcs": spec.srcs,
        "pkg_files": spec.pkg_files,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


class PrimeFailure(RuntimeError):
    """Raised when the populate-cache tool exits non-zero."""

    def __init__(self, returncode: int, stderr: str) -> None:
        super().__init__(
            f"latex_fingerprint_cache prime failed with exit code {returncode}"
        )
        self.returncode = returncode
        self.stderr = stderr


def _extract_snapshot(layout: CacheLayout) -> None:
    """Publish an immutable extraction and atomically switch the pointer.

    Compiles receive the resolved generation path, never the mutable
    ``layout.extracted`` symlink. Old generations remain available to
    in-flight readers after a re-prime. Callers hold ``layout.lock``.
    """
    import tempfile

    digest = hashlib.sha256()
    with open(layout.snapshot, "rb") as snapshot:
        for block in iter(lambda: snapshot.read(1024 * 1024), b""):
            digest.update(block)
    generation = layout.base / f"cache-{digest.hexdigest()}"
    marker = generation / _EXTRACTED_SENTINEL_NAME
    if not marker.is_file():
        if generation.exists():
            # An interrupted, unpublished extraction cannot have readers.
            shutil.rmtree(generation)
        tmp_dir = Path(tempfile.mkdtemp(prefix="cache.tmp.", dir=str(layout.base)))
        try:
            import tarfile

            with tarfile.open(layout.snapshot, "r:gz") as tar:
                try:
                    tar.extractall(tmp_dir, filter="data")
                except TypeError:
                    tar.extractall(tmp_dir)
            (tmp_dir / _EXTRACTED_SENTINEL_NAME).write_text(
                "complete\n", encoding="utf-8",
            )
            os.rename(tmp_dir, generation)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    link_tmp = layout.base / f".cache-link-{os.getpid()}-{time.time_ns()}"
    try:
        link_tmp.symlink_to(generation.name, target_is_directory=True)
        os.replace(link_tmp, layout.extracted)
    finally:
        link_tmp.unlink(missing_ok=True)


def run_prime(
    layout: CacheLayout,
    spec: PrimeSpec,
    workspace: Path,
    *,
    log: callable = print,
) -> float:
    """Prime the persistent cache snapshot (and its extracted form).

    Acquires an exclusive ``flock(2)`` on the layout's lock file so
    two simultaneous serve-startup attempts don't race. If a
    different process has already primed by the time we get the
    lock, returns immediately (lock-then-recheck pattern).

    Writes the snapshot via ``tools/tectonic_populate_cache.py``,
    which:
      * stages the document sources into a temp work directory,
      * runs ``tectonic`` ONCE in online mode,
      * tars the resulting cache directory deterministically into
        the snapshot path.

    After the snapshot is written, publishes an immutable extraction
    and atomically points ``layout.extracted`` at it. Compiles use
    the resolved generation path, skipping per-action decompression.

    The populate tool is invoked with ``workspace`` as its cwd so
    workspace-relative paths in the spec resolve correctly. Tool
    paths in the spec (``tectonic``, ``populate_tool``, ``biber``)
    must already be absolute; document-source paths
    (``main``, ``srcs``, ``pkg_files[].0``) must be
    workspace-relative.

    Returns the elapsed time in seconds.
    """
    layout.base.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()

    with _exclusive_lock(layout.lock):
        # Lock-then-recheck: another process may have completed
        # the prime + extract while we waited for the lock.
        if is_primed(layout) and is_extracted(layout):
            return time.monotonic() - start

        primed_now = not is_primed(layout)
        if primed_now:
            # Build the populate-cache command. We deliberately
            # invoke the tool with the same wire shape
            # latex_document.bzl uses for the implicit-pipeline
            # populate action -- see
            # tools/tectonic_populate_cache.py.
            cmd: list[str] = [
                sys.executable,
                str(spec.populate_tool),
                "--tectonic",
                str(spec.tectonic),
                "--main",
                spec.main,
                "--output",
                str(layout.snapshot),
                "--bundle-url",
                spec.bundle_url,
            ]
            for src in spec.srcs:
                cmd.extend(["--src", src])
            for src, rel in spec.pkg_files:
                cmd.extend(["--pkg-file", f"{src}={rel}"])
            if spec.biber is not None:
                cmd.extend(["--biber", str(spec.biber)])
            for package in spec.ctan_packages:
                cmd.extend(["--ctan-package", package])
            if spec.ctan_packages:
                if spec.bundle_manifest is None:
                    raise ValueError("CTAN priming requires a bundle manifest")
                cmd.extend(["--bundle-manifest", str(spec.bundle_manifest)])
            if spec.ctan_lock is not None:
                cmd.extend(["--ctan-lock", str(spec.ctan_lock)])

            env = os.environ.copy()
            env["LC_ALL"] = "C.UTF-8"
            if spec.use_system_biber:
                env.setdefault("PATH", "/usr/bin:/bin")

            log(
                "latex_live: priming serve cache "
                f"({layout.snapshot}); this may take 30-90s on first run...",
            )
            result = subprocess.run(
                cmd,
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                sys.stderr.write(result.stderr)
                sys.stderr.flush()
                raise PrimeFailure(result.returncode, result.stderr)

            # Mark the snapshot as complete only AFTER the populate
            # tool succeeded -- otherwise an interrupted prime
            # would leave a half-written tarball + matching
            # sentinel and the next build would silently fail with
            # --only-cached.
            layout.sentinel.write_text(
                f"primed at {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n",
                encoding="utf-8",
            )

        # A successful re-prime must publish its own generation even
        # while the previous generation remains complete and readable.
        if primed_now or not is_extracted(layout):
            _extract_snapshot(layout)

    elapsed = time.monotonic() - start
    log(f"latex_live: prime completed in {elapsed:.1f}s")
    return elapsed


def invalidate_for_reprime(layout: CacheLayout) -> None:
    """Mark the cache as needing a re-prime.

    Called when a compile fails with a missing-resource error,
    indicating the user added a new ``\\usepackage`` (or similar)
    whose resources aren't in the current snapshot.

    Hold the same lock as ``run_prime`` and remove only the snapshot
    sentinel. The published extraction remains complete for in-flight
    compiles; a new immutable generation replaces its pointer after
    the next successful prime.
    """
    with _exclusive_lock(layout.lock):
        try:
            layout.sentinel.unlink()
        except FileNotFoundError:
            pass


# Regex set used to recognise a tectonic ``--only-cached`` failure
# caused by missing resources. The text comes from
# ``tectonic_bundles::cache::Cache`` and is stable across the
# tectonic versions we ship; we still match conservatively because
# a false positive only triggers a (legitimate) re-prime, while a
# false negative would leave the user staring at a build error
# with no auto-recovery.
_MISSING_RESOURCE_PATTERNS = (
    re.compile(rb"could not open package file"),
    re.compile(rb"file [^\n]+ not found in cache"),
    re.compile(rb"only-cached.*not found"),
    re.compile(rb"Metric \(TFM\) file or installed font not found"),
    re.compile(rb"File `[^']+\.(?:sty|cls|def|cfg|fd|clo|tfm|otf|ttf|pfb|map)' not found"),
)


def looks_like_missing_resource(stderr_bytes: bytes) -> bool:
    """Heuristic: did this build failure look like a missing cached
    resource? Used to decide whether to auto-re-prime + retry."""
    for pattern in _MISSING_RESOURCE_PATTERNS:
        if pattern.search(stderr_bytes):
            return True
    return False


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


class _exclusive_lock:
    """Context manager: hold an exclusive flock on ``path``.

    Creates the file if it doesn't exist. Releases the lock on
    exit (even on exceptions). On platforms without ``flock`` --
    in practice nothing we support, but we degrade gracefully --
    becomes a no-op and concurrent primes are merely racy, not
    broken (the populate tool writes atomically via a temp dir).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> "_exclusive_lock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(
            self._path,
            os.O_CREAT | os.O_RDWR,
            0o644,
        )
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except (AttributeError, OSError) as e:
            if isinstance(e, OSError) and e.errno not in (errno.ENOTSUP, errno.EINVAL):
                raise
        return self

    def __exit__(self, *_exc_info) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except (AttributeError, OSError):
                pass
            os.close(self._fd)
            self._fd = None
