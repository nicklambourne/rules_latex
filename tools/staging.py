#!/usr/bin/env python3
"""Source-staging logic shared between the tectonic action wrappers.

This module is consumed by `tools/tectonic_populate_cache.py` (the
PopulateCache action) and `tools/tectonic_compile.py` (the
TectonicCompile action). Centralising the staging contract in one
place keeps the two action paths byte-identical from a path-
resolution point of view, which is the whole point of the
main-rooted layout introduced in rules_latex v0.3.

Design contract
---------------

Sources are staged under a work directory using a **main-rooted
layout**:

* The main `.tex` file lands at ``<work>/<main.basename>``.
* Every src that is a descendant of main's package directory is
  staged at the same path relative to main's package directory,
  rooted at the work directory. Example: if main is
  ``study/honours/thesis/thesis/main.tex`` and a src is
  ``study/honours/thesis/thesis/sections/intro.tex``, the src is
  staged at ``<work>/sections/intro.tex``.
* Every src that lives outside main's package is staged under a
  path keyed by its full short_path. Example: a src at
  ``study/llb/lib/references/refs.bib`` (cross-package from a main
  in ``study/llb/1700/notes/``) is staged at
  ``<work>/study/llb/lib/references/refs.bib``.
* Generated files (bazel-out paths) are normalised: the
  ``bazel-out/<config>/bin/`` prefix is stripped so the staged path
  matches what a hand-written source at the same package would
  produce.
* The caller can override placement of any specific input via
  ``pkg_files`` (a list of ``(src_path, staged_relative_path)``
  pairs). Overrides take precedence over the auto-layout.

Rationale
---------

The auto-layout produces paths that LaTeX authors can address
without traversing `..` (which tectonic refuses to hand to external
tools like biber). It also means a document writing
``\\input{sections/foo}`` works identically:

* in editor-driven local compiles (cwd at main's package),
* in TectonicCompile (cwd at the staged work directory),
* in PopulateCache (cwd at the staged work directory).

For the rare case where the natural staged path is awkward —
typically when a deeply-nested cross-package bib file would force
the author to write a long ``\\addbibresource`` argument — the
``pkg_files`` override lets the user place the file wherever they
want, including as a sibling of main.

Materialisation strategy
------------------------

Staged files are materialised by trying, in order:

1. ``os.link`` (hard link). Cheapest: no copy, no readlink-time
   overhead. Same-filesystem only.
2. ``os.symlink``. Works across filesystems; one ``readlink``
   syscall when the file is opened by tectonic.
3. ``shutil.copyfile``. Fallback for platforms where neither of
   the above is permitted (Windows without developer mode and
   without admin) or for filesystems that don't support either.

Empirically the win over unconditional copy is ~5–50 ms per
``stage_sources`` call depending on source-set size, which matters
on the live-preview hot path where ``stage_sources`` runs on every
keystroke save. None of this changes the on-disk layout that
tectonic and biber see: each scheme produces a regular file at
the staged path.

The work directory is per-action and torn down at action end
(``tempfile.TemporaryDirectory`` in both action wrappers), so the
"self-contained snapshot" rationale that motivated the original
copy-only path doesn't apply: nothing reads from the staged tree
after tectonic exits. Hard-linking is therefore safe.

Determinism
-----------

``stage_sources`` is a pure function of its inputs: identical
``main + srcs + pkg_files`` always produces the same file layout.
This matters because the staged work directory feeds into a
content-addressed cache (the PopulateCache tarball), and any
non-determinism in the layout would invalidate that cache
unnecessarily.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, NamedTuple


_BAZEL_OUT_PREFIX = "bazel-out"
_BAZEL_BIN_SEGMENT = "bin"


class PkgFile(NamedTuple):
    """One (input, staged-relative-path) override.

    ``src`` is the path on disk (typically a Bazel execroot-relative
    path passed by the calling action). ``rel`` is the path under the
    work directory where the input should appear.
    """

    src: Path
    rel: str


class StagingError(Exception):
    """Raised on impossible-to-stage configurations.

    Examples: a `pkg_files` override that tries to place two
    different inputs at the same relative path, or that escapes the
    work directory via `..`.
    """


def normalise_short_path(p: Path) -> Path:
    """Strip the ``bazel-out/<config>/bin/`` prefix from a generated
    file's path, returning the path as it would appear in source.

    For non-generated paths this is a no-op.

    >>> normalise_short_path(Path("bazel-out/k8-fastbuild/bin/pkg/file.tex")).as_posix()
    'pkg/file.tex'
    >>> normalise_short_path(Path("pkg/file.tex")).as_posix()
    'pkg/file.tex'
    """
    parts = p.parts
    if (
        len(parts) >= 3
        and parts[0] == _BAZEL_OUT_PREFIX
        and parts[2] == _BAZEL_BIN_SEGMENT
    ):
        return Path(*parts[3:])
    return p


def _main_package(main: Path) -> Path:
    """The directory containing the main file, as a workspace-rooted
    path (i.e. the package directory).

    >>> _main_package(Path("study/honours/thesis/thesis/main.tex")).as_posix()
    'study/honours/thesis/thesis'
    """
    return normalise_short_path(main).parent


def compute_staged_path(src: Path, main_package: Path) -> Path:
    """Return the relative path under the work directory where ``src``
    should be staged in the main-rooted layout.

    The rule:

    * If ``src`` is under ``main_package``, return its path relative
      to ``main_package``.
    * Otherwise return its workspace-relative path (i.e. its
      short_path, with any bazel-out prefix stripped).

    >>> compute_staged_path(
    ...     Path("study/honours/thesis/thesis/sections/intro.tex"),
    ...     Path("study/honours/thesis/thesis"),
    ... ).as_posix()
    'sections/intro.tex'
    >>> compute_staged_path(
    ...     Path("study/llb/lib/references/refs.bib"),
    ...     Path("study/llb/1700/notes"),
    ... ).as_posix()
    'study/llb/lib/references/refs.bib'
    """
    normalised = normalise_short_path(src)
    try:
        rel = normalised.relative_to(main_package)
        return rel
    except ValueError:
        return normalised


def _materialise(src: Path, dest: Path) -> str:
    """Materialise ``src`` at ``dest``, trying link strategies in
    decreasing order of cheapness.

    Returns the name of the strategy that succeeded
    (``"hardlink"``, ``"symlink"``, or ``"copy"``), exposed for
    tests. The caller has already created ``dest.parent`` and
    cleared any pre-existing entry at ``dest``.

    ``src`` is resolved to an absolute path for symlink targets so
    the link stays valid even when the staging tmpdir is deeper in
    the filesystem than the caller's cwd.
    """
    abs_src = os.fspath(src.resolve())
    abs_dest = os.fspath(dest)
    # Hardlink first: cheapest at runtime (no extra syscall on
    # open) and same on-disk semantics as a copy. Fails on
    # cross-filesystem and on Windows for non-admin/non-developer
    # users; both are fall-through.
    try:
        os.link(abs_src, abs_dest)
        return "hardlink"
    except (OSError, NotImplementedError):
        pass
    # Symlinks work across filesystems and on macOS/Linux always.
    # On Windows they require developer mode or admin; fall back
    # to copy if not.
    try:
        os.symlink(abs_src, abs_dest)
        return "symlink"
    except (OSError, NotImplementedError):
        pass
    shutil.copyfile(abs_src, abs_dest)
    return "copy"


def stage_sources(
    main: Path,
    srcs: Iterable[Path],
    pkg_files: Iterable[PkgFile],
    work_dir: Path,
) -> Path:
    """Stage ``main`` and all ``srcs`` into ``work_dir`` under the
    main-rooted layout. Apply ``pkg_files`` overrides last.

    All ``src`` paths (and ``main``) must be **workspace-relative**
    (or, equivalently, bazel execroot-relative). Absolute paths are
    rejected: the layout depends on stripping a known prefix, and an
    arbitrary absolute path has no such prefix.

    Returns the path to the staged main file (relative paths in
    `main.tex` resolve against this file's parent).

    Raises ``StagingError`` if the overrides conflict with each other
    or escape the work directory.
    """
    if main.is_absolute():
        raise StagingError(
            f"main {main} must be a workspace-relative path, not absolute"
        )
    for src in srcs:
        if src.is_absolute():
            raise StagingError(
                f"src {src} must be a workspace-relative path, not absolute"
            )
    main_pkg = _main_package(main)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Track placements so we can detect conflicts.
    placements: dict[Path, Path] = {}

    def _place(src: Path, rel: Path) -> None:
        # Reject paths that escape work_dir. Use string-level check
        # against ``..`` segments so we don't depend on the work_dir
        # actually existing on disk (it may be a future location).
        if ".." in rel.parts or rel.is_absolute():
            raise StagingError(
                f"staged path {rel} for {src} escapes the work directory"
            )
        # Reject conflicting placements.
        existing = placements.get(rel)
        if existing is not None:
            if existing != src:
                raise StagingError(
                    f"two different inputs would be staged at the same path "
                    f"{rel}: {existing} and {src}. Use pkg_files to override "
                    "placement for one of them."
                )
            # Same src at same rel: already placed, idempotent no-op.
            # The repeat is normal under the action wrappers, which
            # pass `main` in both --main and --src (see
            # tools/tectonic_compile.py).
            return
        placements[rel] = src
        dest = work_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Hardlink > symlink > copy. The staging tmpdir is torn
        # down at action end so the "self-contained snapshot"
        # property the old copy-only path provided isn't needed.
        _materialise(src, dest)

    # Auto-staged inputs first.
    main_rel = compute_staged_path(main, main_pkg)
    _place(main, main_rel)
    for src in srcs:
        rel = compute_staged_path(src, main_pkg)
        _place(src, rel)

    # User-declared overrides last so they win on path conflicts.
    for entry in pkg_files:
        rel = Path(entry.rel)
        if rel.is_absolute():
            raise StagingError(
                f"pkg_files staged path {rel} must be relative to the "
                "work directory; got an absolute path."
            )
        if ".." in rel.parts:
            raise StagingError(
                f"pkg_files staged path {rel} contains '..' and would "
                "escape the work directory"
            )
        # An override replaces whatever was there before.
        placements[rel] = entry.src
        dest = work_dir / rel
        # Clean any previous file at this location so the override
        # truly wins. On a hardlinked staging tree the unlink only
        # decrements the source file's link count; the source is
        # untouched.
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        _materialise(entry.src, dest)

    return work_dir / main_rel
