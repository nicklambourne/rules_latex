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

Staged files are independent copies. Tectonic and biber can write
output-like filenames in their working directory, including names
also present among declared inputs. Hardlinks and symlinks would
forward those writes to the original input, including during local
or live-preview compiles where Bazel does not sandbox the action.

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
    # Runfiles short_path for an external repository begins with
    # ../<repo>, while execroot actions see external/<repo>.
    # Normalize only the logical staged location; callers still read
    # the original physical path when copying the file.
    if len(parts) >= 3 and parts[0] == "..":
        return Path("external", *parts[1:])
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
    """Copy an input to independent storage in the staging directory."""
    shutil.copyfile(src, dest)
    return "copy"


def stage_sources(
    main: Path,
    srcs: Iterable[Path],
    pkg_files: Iterable[PkgFile],
    work_dir: Path,
) -> Path:
    """Stage ``main`` and all ``srcs`` into ``work_dir`` under the
    main-rooted layout. Resolve ``pkg_files`` before copying inputs.

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
    main_rel = compute_staged_path(main, main_pkg)
    if main_rel.is_absolute() or ".." in main_rel.parts:
        raise StagingError(f"staged main path {main_rel} escapes the work directory")
    overrides: dict[Path, Path] = {}
    overridden_sources: set[Path] = set()
    for entry in pkg_files:
        rel = Path(entry.rel)
        if rel == Path("."):
            raise StagingError("pkg_files staged path must name a file")
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
        existing = overrides.get(rel)
        if existing is not None and existing != entry.src:
            raise StagingError(
                f"two pkg_files inputs target {rel}: {existing} and {entry.src}"
            )
        if rel == main_rel and entry.src != main:
            raise StagingError(f"pkg_files cannot replace main file at {main_rel}")
        overrides[rel] = entry.src
        overridden_sources.add(entry.src)

    placements: dict[Path, Path] = {main_rel: main}
    for src in srcs:
        if src in overridden_sources and src != main:
            continue
        rel = compute_staged_path(src, main_pkg)
        if rel.is_absolute() or ".." in rel.parts:
            raise StagingError(f"staged path {rel} for {src} escapes the work directory")
        existing = placements.get(rel)
        if existing is not None and existing != src and rel not in overrides:
            raise StagingError(
                f"two different inputs would be staged at the same path "
                f"{rel}: {existing} and {src}. Use pkg_files to override "
                "placement for one of them."
            )
        placements[rel] = src

    # Explicit destinations replace auto-placement only after every
    # override and collision has been validated.
    placements.update(overrides)
    for rel in placements:
        for parent in rel.parents:
            if parent in placements:
                raise StagingError(
                    f"staged file {parent} conflicts with nested file {rel}"
                )
    work_dir.mkdir(parents=True, exist_ok=True)
    for rel, src in placements.items():
        dest = work_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        _materialise(src, dest)

    return work_dir / main_rel
