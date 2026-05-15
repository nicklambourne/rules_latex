# rules_latex — Design

This document captures the goals, non-goals, key design decisions, and
known open questions for `rules_latex`. It is meant to be read alongside the
[README](./README.md), which focuses on user-facing behaviour.

## 1. Goals

1. **Make Bazel-based LaTeX builds painless.** Users should be able to drop a
   `latex_document(...)` into a `BUILD.bazel` and have it Just Work, without
   enumerating which LaTeX packages their document uses or maintaining patches
   against an underlying ruleset.
2. **Modern Bazel hygiene.** Bzlmod from day one; toolchain-based; platform
   constraints handled via `@platforms`; no legacy WORKSPACE entry point.
3. **Hermeticity.** Pinned, content-addressed Tectonic binaries; an offline
   package bundle path for environments that disallow network at build time.
4. **Composability.** Documents can `dep` on libraries (shared preambles,
   class files) and resource packages (images, `.bib` files) without weird
   workarounds.
5. **Small, legible codebase.** Easy to read end-to-end; easy to contribute
   to; easy to fork if Tectonic ever stops being the right backend.

## 2. Non-goals (for v0.1)

- **Wrapping pdfTeX/XeTeX/LuaTeX directly.** Tectonic is the only backend.
  Multi-engine support is a possible future direction but multiplies the
  toolchain surface area.
- **Building Tectonic from source via `rules_rust`.** The official prebuilt
  binaries are sufficient for the 99% case.
- **Per-document package isolation.** Tectonic resolves packages from a single
  shared bundle; we don't try to virtualise that further.

## 3. Comparison with alternatives

| Aspect                      | `bazel_latex`                              | `rules_latex` (this repo)              |
|-----------------------------|--------------------------------------------|----------------------------------------|
| Backend                     | TeX Live (full distribution)               | Tectonic (XeTeX + custom driver)       |
| Package management          | Explicit Bazel labels per `.sty`           | Implicit, by Tectonic at compile time  |
| Hermeticity                 | Strong (sandboxed TeX Live)                | Strong with offline bundle; opt-in     |
| WORKSPACE / Bzlmod          | Both, leans WORKSPACE                      | Bzlmod-only                            |
| First build cost            | Many MB of TeX Live fetched as needed      | ~20 MB tectonic binary, ~few MB bundle |
| Maintenance burden          | Patches needed against rule internals      | Single dependency: tectonic            |

## 4. Architecture

### 4.1 Public API surface

Loaded from `@rules_latex//latex:defs.bzl`:

- `latex_document(name, main, srcs, deps = [], outfmt = "pdf", reproducible = False, cache = None, tectonic_args = [])`
- `latex_library(name, srcs, deps = [])`
- `latex_pkg(name, srcs)`
- `latex_test(name, main, srcs, deps = [], outfmt = "pdf", cache = None, forbidden_patterns = [], forbidden_patterns_replace = False, required_patterns = [])`
- `latex_cache_snapshot(name, main, srcs, deps = [], output)`
- `LatexInfo` provider (for users authoring their own rules)

The toolchain type is exported at `@rules_latex//latex:toolchain_type` for
custom toolchain registrations.

### 4.2 Toolchain model

A single `toolchain_type` (`//latex/toolchain:toolchain_type`) is consumed by
every rule that invokes Tectonic.

A `latex_toolchain` rule packages two attributes:

| Field    | Description                                                           |
|----------|-----------------------------------------------------------------------|
| `tectonic` | The Tectonic executable for the target platform.                    |
| `bundle`   | Optional offline package bundle (`.tar`). If set, the rule passes `--bundle <path>` and Tectonic runs with no network access. |

A `tectonic` module extension (`@rules_latex//latex/toolchain:extensions.bzl`)
materialises one `tectonic_repository` per supported platform and a single
`rules_latex_tectonic_toolchains` "hub" repository that registers a
`toolchain(...)` for each one, gated by `exec_compatible_with` / `target_compatible_with`.

The pinned Tectonic version and per-platform SHA256 hashes live in
[`latex/private/versions.bzl`](./latex/private/versions.bzl).

### 4.3 Action model

`latex_document` produces one output file (the PDF, by default).
Internally it invokes:

    tectonic -X compile \
        --outfmt <pdf|html|xdv|aux> \
        --outdir <bazel-out-dir> \
        [--bundle <bundle.tar> --only-cached] \
        --keep-logs \
        [user-supplied tectonic_args ...] \
        <main.tex>

Sources are gathered transitively from `srcs` plus every `LatexInfo` provider
exposed by `deps`. The bundle, if present, is an action input so it
participates in Bazel's content-based caching. When a bundle is supplied we
also pass `--only-cached`, which causes Tectonic to refuse any network
access.

### 4.4 Network policy

By default, Tectonic fetches its package bundle on first run from
`relay.fullyjustified.net`. This is convenient but non-hermetic and a single
point of failure.

`rules_latex` supports three modes:

1. **Online mode (default).** No `tectonic.bundle()` tag in the consumer's
   `MODULE.bazel` and no per-document `cache = ...`; Tectonic reaches out to
   fetch packages on first run, caching them in a per-action scratch
   directory. Documented as "fine for local dev, not for CI".
2. **Full bundle.** When `tectonic.bundle()` is declared on the `tectonic`
   module extension, a `tectonic_bundle_repository` http-fetches the pinned
   bundle (`tlextras-2021.3r1.tar`, sha256 published alongside the
   tectonic-typesetting/tectonic-texlive-bundles GitHub release) and feeds
   it into every materialised `latex_toolchain`. Actions run with
   `--bundle <path>` and `--only-cached`, no network access required at
   build time. The downside: every first build fetches ~3 GB.
3. **Per-document cache snapshot.** A `latex_cache_snapshot` target is run
   once with `bazel run` to compile the document in online mode, capture
   the resulting tectonic cache directory (typically 10–100 MB depending
   on the document), and tar it up reproducibly into the source tree. The
   `latex_document(cache = ...)` attribute then consumes that snapshot:
   the action extracts it into `$TECTONIC_CACHE_DIR` and runs with
   `--only-cached`, producing a fully hermetic build that doesn't pull the
   full bundle. This is much smaller than the full bundle approach,
   content-addressed, and only needs refreshing when the document starts
   `\\usepackage`'ing something new. See
   [`latex/private/latex_cache_snapshot.bzl`](./latex/private/latex_cache_snapshot.bzl)
   and [`tools/make_cache_snapshot.py`](./tools/make_cache_snapshot.py).

Snapshot mode and full-bundle mode are not exclusive — they coexist on a
per-target basis: the `cache = ...` attribute always wins over the
toolchain-level bundle when both are set.

### 4.5 Reproducibility

By default Tectonic embeds the current wall-clock time as the PDF's
creation/modification date, so identical inputs produce non-byte-identical
PDFs. `latex_document(reproducible = True)` flips on both
`SOURCE_DATE_EPOCH=0` and Tectonic's `-Z deterministic-mode`, which together
produce byte-identical output across clean builds. SyncTeX output is broken
by this flag (Tectonic warns about this); leave it off if you care about
SyncTeX.

### 4.6 Sandbox compatibility

Tectonic by default derives its cache directory from `$XDG_CACHE_HOME` /
`$HOME`, both of which are unset under Bazel's Linux sandbox. To avoid
"Read-only file system (os error 30)" on first invocation, each
`TectonicCompile` action runs through a tiny shell wrapper that allocates a
per-action `mktemp -d` scratch dir and exports it as `TECTONIC_CACHE_DIR`.
The wrapper also propagates `LC_ALL=C.UTF-8` (some downstream helpers like
`biber` insist on a UTF-8 locale).

## 5. Open questions / future work

These are deliberately out of scope for v0.1 but worth flagging.

1. **Tectonic v2 workspace mode.** Tectonic v2 introduced a project format
   with `Tectonic.toml`. Worth supporting eventually, but the simpler
   `-X compile <main.tex>` invocation is enough for v0.1.
2. **`biber` / `bibtex` / `makeindex` toolchain attrs.** Tectonic vendors
   these internally, but advanced workflows may want to swap them. Add as
   optional fields on `latex_toolchain` later if there's demand.
3. **`latex_lint`.** Wraps `chktex` / `lacheck`. Could ship as an optional
   toolchain.
4. **Bundle updates.** The current pinned bundle is the upstream tlextras
   2021.3r1, which is still being served from the CDN as of writing.
   Upstream has not cut a newer tlextras release; we should track that
   repo and bump when they do.
5. **Caching of intermediate aux files.** Tectonic is fast and Bazel caches
   the action output, so this is probably never worth doing — but worth
   benchmarking on multi-pass documents (e.g. with biblatex).

## 6. Versioning

`rules_latex` will follow semver post-1.0. Pre-1.0 releases (v0.x) can break
API freely; expect every rule to potentially change shape.

## 7. Release process

- Tag `vX.Y.Z` on `master`. The `.github/workflows/release.yml` workflow
  runs automatically and:
  - Verifies the tag matches the `version = ...` field in `MODULE.bazel`.
  - Produces `rules_latex-X.Y.Z.tar.gz` via `git archive`.
  - Computes its sha256 and a BCR-formatted `integrity = "sha256-…"` hash.
  - Publishes a GitHub Release with the archive and BCR submission
    snippet in the release notes.
- The Bazel Central Registry PR is opened manually (one-time per
  release) using the snippet from the release notes.

The post-tag bits below are still manual:

- Drafting `CHANGELOG.md` entries before tagging.
- Opening the BCR PR against
  [`bazelbuild/bazel-central-registry`](https://github.com/bazelbuild/bazel-central-registry).
